"""Musik-KI: ComfyUI (portabel, AMD ROCm oder NVIDIA) mit ACE-Step 1.5.

- install():   ComfyUI + Modelle laden (mit Fortsetzen abgebrochener Downloads)
- start()/stop(): ComfyUI im Hintergrund starten/beenden (nur, wenn MusicBox es gestartet hat)
- generate():  einen Song erzeugen und in den Songs-Ordner legen

Grafiktreiber werden nie angefasst – nur ein Hinweis, wenn er zu alt ist.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import config as C
from .debuglog import log

WIN = sys.platform == "win32"
NOWIN = 0x08000000 if WIN else 0
UA = {"User-Agent": "Ascandir-MusicBox"}

COMFY_DIR = C.DATA / "comfyui"
OUTPUT_DIR = C.DATA / "comfy-output"
TOOLS = C.DATA / "tools"
LOG = C.DATA / "comfy-log.txt"

COMFY_RELEASE_API = "https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest"
COMFY_FALLBACK = {
    "amd": "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_amd.7z",
    "nvidia": "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z",
}
SEVENZIP_API = "https://api.github.com/repos/ip7z/7zip/releases/latest"
SEVENZIP_FALLBACK = "https://www.7-zip.org/a/7zr.exe"
AMD_MIN_DRIVER_DATE = "2026-01-06"

HF = "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files/"
FILES = {
    "vae":      {"name": "ace_1.5_vae.safetensors", "dir": "vae", "gb": 0.3},
    "te":       {"name": "qwen_0.6b_ace15.safetensors", "dir": "text_encoders", "gb": 1.2},
    "lm-1.7b":  {"name": "qwen_1.7b_ace15.safetensors", "dir": "text_encoders", "gb": 3.5},
    "lm-4b":    {"name": "qwen_4b_ace15.safetensors", "dir": "text_encoders", "gb": 8.0},
    "turbo":    {"name": "acestep_v1.5_turbo.safetensors", "dir": "diffusion_models", "gb": 4.8},
    "xl-turbo": {"name": "acestep_v1.5_xl_turbo_bf16.safetensors", "dir": "diffusion_models", "gb": 9.5},
}
for _k, _f in FILES.items():
    _f["url"] = HF + _f["dir"] + "/" + _f["name"]

MUSIK_MODELLE = {        # Anzeige-Name → Schlüssel
    "Schnell & gut (Turbo, 2 Mrd.)": "turbo",
    "Beste Qualität (XL-Turbo, 4 Mrd.)": "xl-turbo",
}
PLANER_MODELLE = {
    "Normal (1,7 Mrd.) – empfohlen für 16 GB": "1.7b",
    "Stark (4 Mrd.) – braucht mehr Grafikspeicher": "4b",
}


# ---------- Hilfen ----------
def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _download(url, dest, progress, label):
    """Lädt mit Fortsetzen (Teil-Datei *.part)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dest) + ".part")
    for attempt in range(4):
        start = tmp.stat().st_size if tmp.exists() else 0
        req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={start}-"} if start else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if start and r.status != 206:
                    start = 0
                total = int(r.headers.get("Content-Length") or 0) + start
                done = start
                last = 0
                with open(tmp, "ab" if start else "wb") as f:
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total and time.time() - last > 0.5:
                            last = time.time()
                            progress(done / total, f"{label} … {done / 1e9:.2f} von {total / 1e9:.2f} GB")
            if total and tmp.stat().st_size < total:
                raise IOError("Download unvollständig")
            tmp.replace(dest)
            return
        except urllib.error.HTTPError as e:
            if e.code == 416 and tmp.exists():        # schon komplett
                tmp.replace(dest)
                return
            if attempt == 3 or e.code in (401, 403, 404):
                raise RuntimeError(f"{label}: Download fehlgeschlagen (HTTP {e.code}) – {url}")
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"{label}: Download fehlgeschlagen ({e})")
        log.info("Download %s abgebrochen – neuer Versuch %d", label, attempt + 2)
        time.sleep(3)


# ---------- Grafikkarte ----------
_gpu_cache = {}


def gpus():
    """Liste der Grafikkarten: [{'name', 'treiber', 'datum', 'vram_gb'}]."""
    if "l" in _gpu_cache:
        return _gpu_cache["l"]
    out = []
    if WIN:
        ps = ("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,"
              "@{n='Date';e={$_.DriverDate.ToString('yyyy-MM-dd')}} | ConvertTo-Json -Compress")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=20, creationflags=NOWIN)
            data = json.loads(r.stdout or "[]")
            data = data if isinstance(data, list) else [data]
            out = [{"name": d.get("Name") or "", "treiber": d.get("DriverVersion") or "",
                    "datum": d.get("Date") or ""} for d in data]
        except Exception as e:
            log.info("Grafikkarten-Abfrage fehlgeschlagen: %s", e)
    _gpu_cache["l"] = out
    return out


def _main_gpu():
    lst = gpus()
    def score(g):
        n = g["name"].lower()
        s = 0
        if "nvidia" in n or "geforce" in n or "rtx" in n:
            s += 10
        if re.search(r"\brx\b|radeon pro|radeon ai", n):
            s += 10                  # dedizierte AMD-Karte (nicht die Prozessor-Grafik)
        elif "radeon" in n:
            s += 3
        return s
    return max(lst, key=score) if lst else None


def gpu_vendor():
    setting = C.settings().get("grafikkarte", "Automatisch")
    if setting == "AMD":
        return "amd"
    if setting == "NVIDIA":
        return "nvidia"
    g = _main_gpu()
    n = (g or {}).get("name", "").lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "quadro")):
        return "nvidia"
    return "amd"


def driver_hint():
    """Hinweistext, wenn der AMD-Treiber zu alt ist – sonst ''."""
    g = _main_gpu()
    if not g:
        return ""
    if gpu_vendor() == "amd" and g.get("datum") and g["datum"] < AMD_MIN_DRIVER_DATE:
        return (f"⚠️ Dein AMD-Treiber ist vom {g['datum']}. Für die Musik-KI (ROCm) wird ein Treiber ab "
                f"Januar 2026 (Adrenalin 26.1.1 oder neuer) empfohlen. Bitte selbst über AMD Adrenalin "
                f"aktualisieren – MusicBox ändert nie etwas an deinem Treiber.")
    return ""


# ---------- Installation ----------
def comfy_root():
    """Ordner mit python_embeded und ComfyUI/main.py – oder None."""
    custom = (C.settings().get("comfy_ordner") or "").strip().strip('"')
    bases = [Path(custom)] if custom else [COMFY_DIR]
    for base in bases:
        if not base.exists():
            continue
        cands = [base] + [p for p in base.iterdir() if p.is_dir()]
        for c in cands:
            if (c / "ComfyUI" / "main.py").exists() and \
                    ((c / "python_embeded" / "python.exe").exists() or (c / "python_embeded" / "python").exists()):
                return c
    return None


def models_dir():
    r = comfy_root()
    return (r / "ComfyUI" / "models") if r else None


def needed_files(st=None):
    st = st or C.settings()
    keys = ["vae", "te", "lm-4b" if st.get("planer_modell") == "4b" else "lm-1.7b",
            "xl-turbo" if st.get("musik_modell") == "xl-turbo" else "turbo"]
    return keys


def file_path(key):
    md = models_dir()
    return (md / FILES[key]["dir"] / FILES[key]["name"]) if md else None


def model_status():
    md = models_dir()
    if not md:
        return "ComfyUI nicht installiert"
    return {k: (file_path(k).exists()) for k in FILES}


def missing_files(st=None):
    out = []
    for k in needed_files(st):
        p = file_path(k)
        if p is None or not p.exists():
            out.append(k)
    return out


def installed(st=None):
    return comfy_root() is not None and not missing_files(st)


def _sevenzip(progress):
    exe = TOOLS / "7zr.exe"
    if exe.exists():
        return exe
    url = SEVENZIP_FALLBACK
    try:
        rel = _get_json(SEVENZIP_API)
        a = next((a for a in rel.get("assets", []) if a["name"].lower() == "7zr.exe"), None)
        if a:
            url = a["browser_download_url"]
    except Exception as e:
        log.info("7-Zip-Release nicht abrufbar (%s) – nehme %s", e, url)
    _download(url, exe, progress, "Entpacker (7-Zip) wird geladen")
    return exe


def _comfy_url(vendor):
    try:
        rel = _get_json(COMFY_RELEASE_API)
        assets = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".7z")]
        exact = f"comfyui_windows_portable_{vendor}.7z"
        a = next((a for a in assets if a["name"].lower() == exact), None) or \
            next((a for a in assets if vendor in a["name"].lower()), None)
        if a:
            log.info("ComfyUI %s: %s", rel.get("tag_name"), a["name"])
            return a["browser_download_url"], rel.get("tag_name", "")
    except Exception as e:
        log.info("ComfyUI-Release nicht abrufbar (%s) – nehme Standard-Link", e)
    return COMFY_FALLBACK[vendor], ""


def _extract(archive, dest, progress):
    exe = _sevenzip(lambda f, m: progress(0.0, m))
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "x", str(archive), f"-o{dest}", "-y", "-bsp1", "-bso0"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN)
    buf = b""
    while True:
        ch = p.stdout.read(64)
        if not ch:
            break
        buf = (buf + ch)[-200:]
        m = re.findall(rb"(\d{1,3})%", buf)
        if m:
            progress(int(m[-1]) / 100, f"Musik-KI wird entpackt … {int(m[-1])} %")
    if p.wait() != 0:
        raise RuntimeError("Entpacken von ComfyUI fehlgeschlagen.")


def install(progress=lambda f, m: None, only_models=False):
    """ComfyUI (falls nötig) + fehlende Modelle laden. progress(anteil 0..1, text)."""
    st = C.settings()
    did_comfy = False
    if comfy_root() is None and not only_models:
        if st.get("comfy_ordner"):
            raise RuntimeError(f"Im eingestellten ComfyUI-Ordner wurde kein ComfyUI gefunden: {st['comfy_ordner']}")
        vendor = gpu_vendor()
        url, tag = _comfy_url(vendor)
        dl = C.DATA / "downloads"
        archive = dl / Path(urllib.parse.urlparse(url).path).name
        log.info("Lade ComfyUI (%s): %s", vendor, url)
        _download(url, archive, lambda f, m: progress(f * 0.25, m),
                  f"Musik-KI-Programm (ComfyUI für {'AMD' if vendor == 'amd' else 'NVIDIA'}) wird geladen")
        tmp = C.DATA / "comfyui.neu"
        shutil.rmtree(tmp, ignore_errors=True)
        _extract(archive, tmp, lambda f, m: progress(0.25 + f * 0.05, m))
        shutil.rmtree(COMFY_DIR, ignore_errors=True)
        tmp.rename(COMFY_DIR)
        archive.unlink(missing_ok=True)
        C.save("comfy", {"version": tag, "vendor": vendor, "installiert": time.strftime("%Y-%m-%d")})
        if comfy_root() is None:
            raise RuntimeError("ComfyUI wurde entpackt, aber nicht gefunden. Bitte Debug-Log schicken.")
        did_comfy = True
    base = 0.3 if did_comfy else 0.0
    miss = missing_files(st)
    total_gb = sum(FILES[k]["gb"] for k in miss) or 1
    done_gb = 0.0
    for k in miss:
        f = FILES[k]
        share = f["gb"] / total_gb
        lo = base + (1 - base) * done_gb / total_gb
        _download(f["url"], file_path(k),
                  lambda x, m, lo=lo, share=share: progress(lo + (1 - base) * share * x, m),
                  f"KI-Modell {f['name']} wird geladen")
        done_gb += f["gb"]
    progress(1.0, "Musik-KI ist bereit.")


# ---------- Start / Stopp ----------
URL = f"http://127.0.0.1:{C.COMFY_PORT}"
_proc = None
_started = False
_last_use = 0.0
_lock = threading.RLock()


def _api(path, data=None, timeout=10):
    req = urllib.request.Request(URL + path, headers={**UA, "Content-Type": "application/json"},
                                 data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def running():
    try:
        _api("/system_stats", timeout=2)
        return True
    except Exception:
        return False


def _log_tail(n=15):
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except Exception:
        return ""


GPU_FLAGS = {
    "Schonend": ["--reserve-vram", "2.5", "--disable-dynamic-vram", "--disable-pinned-memory",
                 "--disable-async-offload", "--disable-smart-memory", "--use-split-cross-attention"],
    "Ausgewogen": ["--reserve-vram", "1.5", "--disable-pinned-memory", "--disable-async-offload"],
    "Volle Leistung": [],
}


def launch_cmd(root):
    """(Befehl, Umgebung) – liest die Einstellungen aus run_amd_gpu.bat / run_nvidia_gpu.bat."""
    env = dict(os.environ)
    args = ["--windows-standalone-build"]
    for bat in ("run_amd_gpu.bat", "run_nvidia_gpu.bat"):
        p = root / bat
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = re.match(r'^\s*set\s+"?([A-Za-z0-9_]+)=([^"]*)"?\s*$', line, re.I)
            if s:
                env[s.group(1)] = s.group(2)
            m = re.search(r"main\.py\s+(.*)$", line, re.I)
            if m:
                args = [a for a in m.group(1).split() if a and not re.match(r"^%|pause", a, re.I)]
        break
    args = [a for a in args if a not in ("--listen", "--port", "--auto-launch")]
    mode = C.settings().get("gpu_modus", "Ausgewogen")
    extra = list(GPU_FLAGS.get(mode, GPU_FLAGS["Ausgewogen"]))
    # nur Schalter verwenden, die diese ComfyUI-Version kennt
    try:
        known = (root / "ComfyUI" / "comfy" / "cli_args.py").read_text(encoding="utf-8", errors="replace")
        keep, i = [], 0
        while i < len(extra):
            flag = extra[i]
            has_val = i + 1 < len(extra) and not extra[i + 1].startswith("--")
            if f'"{flag}"' in known:
                keep += extra[i:i + 2] if has_val else [flag]
            i += 2 if has_val else 1
        extra = keep
    except Exception:
        pass
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    py = root / "python_embeded" / ("python.exe" if WIN else "python")
    cmd = [str(py), "-s", str(root / "ComfyUI" / "main.py"), *args, *extra, "--disable-auto-launch",
           "--listen", "127.0.0.1", "--port", str(C.COMFY_PORT), "--output-directory", str(OUTPUT_DIR)]
    env.update({"MIOPEN_FIND_MODE": "FAST", "TOKENIZERS_PARALLELISM": "false", "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8"})
    return cmd, env


def touch():
    global _last_use
    _last_use = time.time()


def start(progress=lambda f, m: None):
    global _proc, _started
    with _lock:
        touch()
        if running():
            return
        root = comfy_root()
        if root is None or missing_files():
            install(progress)
            root = comfy_root()
        cmd, env = launch_cmd(root)
        log.info("Musik-KI-Start: %s", cmd)
        logf = open(LOG, "a", encoding="utf-8", errors="replace")
        logf.write(f"\n=== {time.ctime()} Start: {' '.join(cmd)}\n")
        logf.flush()
        _proc = subprocess.Popen(cmd, cwd=str(root), env=env, stdout=logf, stderr=logf,
                                 stdin=subprocess.DEVNULL, creationflags=NOWIN)
        _started = True
        t0 = time.time()
        limit = 900
        while time.time() - t0 < limit:
            if running():
                log.info("Musik-KI bereit nach %.0f s", time.time() - t0)
                touch()
                return
            if _proc.poll() is not None:
                _started = False
                raise RuntimeError("Die Musik-KI hat sich beim Start beendet.\n\nLetzte Meldungen:\n" + _log_tail())
            sec = int(time.time() - t0)
            progress(min(0.95, sec / 120), f"Musik-KI startet … ({sec // 60}:{sec % 60:02d}) – "
                                           "beim allerersten Start kann das ein paar Minuten dauern")
            time.sleep(2)
        stop()
        raise RuntimeError("Die Musik-KI hat nach 15 Minuten nicht geantwortet.\n\n" + _log_tail())


def stop():
    """Beendet ComfyUI – aber nur, wenn MusicBox es gestartet hat."""
    global _proc, _started
    with _lock:
        if not _started:
            return False
        log.info("Musik-KI wird beendet")
        from .llm import _kill_tree, _listener_pids
        if _proc is not None:
            _kill_tree(_proc.pid)
        if WIN and running():
            for pid in _listener_pids(C.COMFY_PORT):
                _kill_tree(pid)
        _proc, _started = None, False
        return True


def started_by_us():
    return _started


def idle_check():
    """Vom Hintergrund-Takt aufgerufen: nach x Minuten ohne Benutzung beenden."""
    mins = float(C.settings().get("musik_leerlauf_min", 10) or 0)
    if _started and mins > 0 and not BUSY.locked() and time.time() - _last_use > mins * 60:
        log.info("Musik-KI %s Min. unbenutzt – wird beendet", mins)
        stop()


# ---------- Song erzeugen ----------
BUSY = threading.Lock()
_cancel = threading.Event()

SPRACHEN = {"Deutsch": "de", "Englisch": "en", "Spanisch": "es", "Französisch": "fr", "Italienisch": "it",
            "Portugiesisch": "pt", "Niederländisch": "nl", "Schwedisch": "sv", "Norwegisch": "no",
            "Dänisch": "da", "Finnisch": "fi", "Polnisch": "pl", "Russisch": "ru", "Türkisch": "tr",
            "Japanisch": "ja", "Koreanisch": "ko", "Chinesisch": "zh", "Latein": "la"}
TONARTEN = [f"{r} {q}" for q in ("major", "minor") for r in
            ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]]
TAKTE = {"4/4": "4", "3/4": "3", "6/8": "6", "2/4": "2"}


def build_graph(p, seed, save_node="SaveAudioAdvanced"):
    st = C.settings()
    unet = FILES["xl-turbo" if st.get("musik_modell") == "xl-turbo" else "turbo"]["name"]
    lm = FILES["lm-4b" if st.get("planer_modell") == "4b" else "lm-1.7b"]["name"]
    secs = float(p["dauer"])
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": FILES["te"]["name"], "clip_name2": lm,
                                                          "type": "ace", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": FILES["vae"]["name"]}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["2", 0], "tags": p["tags"], "lyrics": p["lyrics"], "seed": seed,
            "bpm": int(p["bpm"]), "duration": secs, "timesignature": str(p.get("takt", "4")),
            "language": p.get("sprache", "de"), "keyscale": p.get("tonart", "C major"),
            "generate_audio_codes": bool(st.get("audio_codes", True)), "cfg_scale": 2.0,
            "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": secs, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0],
            "seed": seed, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    }
    # eindeutiger Name: sonst liefert ComfyUI bei gleichem Auftrag nur das alte (schon verschobene) Ergebnis
    prefix = f"musicbox/{uuid.uuid4().hex[:12]}"
    if save_node == "SaveAudioAdvanced":
        g["10"] = {"class_type": "SaveAudioAdvanced", "inputs": {"audio": ["9", 0], "filename_prefix": prefix,
                                                                 "format": "flac"}}
    else:
        g["10"] = {"class_type": "SaveAudio", "inputs": {"audio": ["9", 0], "filename_prefix": prefix}}
    return g


PHASEN = {"1": "📦 Musik-Modell wird geladen", "2": "📦 Song-Planer wird geladen", "3": "📦 Klang-Decoder wird geladen",
          "5": "🎼 Der Song-Planer komponiert (Aufbau, Melodie, Gesang)", "8": "🎛️ Musik wird erzeugt",
          "9": "🔊 Audio wird berechnet", "10": "💾 Wird gespeichert"}


def _friendly(msg):
    low = (msg or "").lower()
    if "out of memory" in low or "hip error" in low or "oom" in low:
        return (msg + "\n\n💡 Der Grafikspeicher hat nicht gereicht. Versuch es mit einem kürzeren Song, unter "
                "⚙️ Einstellungen mit „Grafikkarten-Modus: Schonend“ oder dem Turbo-Modell statt XL. "
                "Schließ auch Spiele und andere KI-Programme.")
    return msg


def _submit(graph, client_id):
    try:
        return _api("/prompt", {"prompt": graph, "client_id": client_id}, timeout=30)["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Die Musik-KI hat den Auftrag abgelehnt: {body[:1500]}")


def _wait(prompt_id, client_id, progress, label):
    """Wartet auf das Ergebnis; Fortschritt per WebSocket, sonst per Abfrage."""
    t0 = time.time()
    state = {"phase": "⏳ In der Warteschlange", "step": None}
    ws = None
    try:
        import websocket                      # websocket-client
        ws = websocket.create_connection(f"ws://127.0.0.1:{C.COMFY_PORT}/ws?clientId={client_id}", timeout=1)
    except Exception as e:
        log.info("WebSocket nicht verfügbar (%s) – frage regelmäßig ab", e)
    last_poll = 0
    try:
        while True:
            if _cancel.is_set():
                try:
                    _api("/interrupt", {})
                    _api("/queue", {"delete": [prompt_id]})
                except Exception:
                    pass
                raise RuntimeError("Abgebrochen.")
            if ws is not None:
                try:
                    raw = ws.recv()
                    if isinstance(raw, str):
                        m = json.loads(raw)
                        d = m.get("data", {}) or {}
                        if d.get("prompt_id") not in (None, prompt_id):
                            pass
                        elif m.get("type") == "executing" and d.get("node"):
                            state["phase"] = PHASEN.get(str(d["node"]), state["phase"])
                            state["step"] = None
                        elif m.get("type") == "progress":
                            state["step"] = (d.get("value"), d.get("max"))
                        elif m.get("type") == "execution_error":
                            raise RuntimeError(_friendly(f"Fehler in der Musik-KI ({d.get('node_type')}): "
                                                         f"{d.get('exception_message', '').strip()}"))
                except RuntimeError:
                    raise
                except Exception:
                    pass                      # Zeitüberschreitung beim Warten – normal
            else:
                time.sleep(1)
            sec = int(time.time() - t0)
            txt = f"{label}: {state['phase']}"
            if state["step"] and state["step"][1]:
                txt += f" – Schritt {state['step'][0]}/{state['step'][1]}"
            txt += f" ({sec // 60}:{sec % 60:02d})"
            progress(None, txt)
            if time.time() - last_poll > (2 if ws is None else 4):
                last_poll = time.time()
                try:
                    h = _api(f"/history/{prompt_id}", timeout=10).get(prompt_id)
                except Exception:
                    h = None
                if h:
                    status = h.get("status", {}) or {}
                    if status.get("status_str") == "error":
                        msgs = [m[1] for m in status.get("messages", []) if m and m[0] == "execution_error"]
                        e = msgs[0] if msgs else {}
                        raise RuntimeError(_friendly(f"Fehler in der Musik-KI ({e.get('node_type', '?')}): "
                                                     f"{(e.get('exception_message') or '').strip()}"))
                    if status.get("completed") or h.get("outputs"):
                        outs = h.get("outputs", {}).get("10", {})
                        files = outs.get("audio") or outs.get("audios") or []
                        if files:
                            return files[0], time.time() - t0
                        if status.get("completed"):
                            raise RuntimeError("Die Musik-KI ist fertig, hat aber keine Audiodatei geliefert.")
            touch()
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _safe_name(s, n=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return (s[:n].strip() or "Song")


def generate(p, progress=lambda f, m: None):
    """p: tags, lyrics, bpm, dauer, takt, sprache, tonart, seed, varianten, titel, meta (dict für die Bibliothek).
    Gibt eine Liste fertiger Song-Dateien zurück."""
    if not BUSY.acquire(blocking=False):
        raise RuntimeError("Es wird gerade schon ein Song erzeugt.")
    _cancel.clear()
    try:
        start(lambda f, m: progress(f, m))
        out = []
        n = max(1, int(p.get("varianten", 1)))
        save_node = "SaveAudioAdvanced"
        for i in range(n):
            seed = int(p["seed"]) + i
            label = f"Variante {i + 1} von {n}" if n > 1 else "Song"
            cid = uuid.uuid4().hex
            try:
                pid = _submit(build_graph(p, seed, save_node), cid)
            except RuntimeError as e:
                if save_node == "SaveAudioAdvanced" and "SaveAudioAdvanced" in str(e):
                    save_node = "SaveAudio"            # ältere ComfyUI-Version
                    pid = _submit(build_graph(p, seed, save_node), cid)
                else:
                    raise
            info, secs = _wait(pid, cid, progress, label)
            src = OUTPUT_DIR / info.get("subfolder", "") / info["filename"]
            if not src.exists():                      # eigener ComfyUI-Ordner mit anderem Ausgabeordner
                req = urllib.parse.urlencode({"filename": info["filename"], "subfolder": info.get("subfolder", ""),
                                              "type": info.get("type", "output")})
                tmp = C.DATA / "tmp-download"
                with urllib.request.urlopen(URL + "/view?" + req, timeout=120) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
                src = tmp
            dest_dir = C.songs_dir()
            stamp = time.strftime("%Y-%m-%d %H-%M")
            base = f"{stamp} {_safe_name(p.get('titel') or 'Song')}" + (f" ({i + 1})" if n > 1 else "")
            dest = dest_dir / (base + src.suffix)
            k = 2
            while dest.exists():
                dest = dest_dir / f"{base} [{k}]{src.suffix}"
                k += 1
            shutil.move(str(src), dest)
            meta = dict(p.get("meta") or {})
            meta.update({"seed": seed, "dauer_erzeugung_s": round(secs), "erstellt": time.strftime("%Y-%m-%d %H:%M"),
                         "musik_modell": C.settings().get("musik_modell"),
                         "planer_modell": C.settings().get("planer_modell"),
                         "tags_ki": p["tags"], "lyrics_ki": p["lyrics"]})
            dest.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Song fertig: %s (%.0f s)", dest, secs)
            out.append(str(dest))
        touch()
        return out
    finally:
        BUSY.release()


def cancel():
    _cancel.set()
    if running():
        try:
            _api("/interrupt", {})
        except Exception:
            pass
