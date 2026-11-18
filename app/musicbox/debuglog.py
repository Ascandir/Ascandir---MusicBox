"""Protokoll aller wichtigen Schritte + Debug-Bericht zum Verschicken."""
import datetime as dt
import json
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
from pathlib import Path

from . import config as C

LOGFILE = C.DATA / "app.log"
log = logging.getLogger("musicbox")
_set_up = False


def setup():
    global _set_up
    if _set_up:
        return
    _set_up = True
    h = logging.handlers.RotatingFileHandler(LOGFILE, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

    def hook(t, v, tb):
        log.error("Unbehandelter Fehler:\n%s", "".join(traceback.format_exception(t, v, tb)))
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    log.info("==== Start, Version %s ====", _version())


def _version():
    p = Path(__file__).resolve().parents[1] / "VERSION"
    return p.read_text().strip() if p.exists() else "?"


def _tail(path, n):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except Exception as e:
        return f"(nicht lesbar: {e})"


def _safe(fn):
    try:
        return fn()
    except Exception as e:
        return f"(Fehler: {e})"


def report():
    from . import llm as L
    from . import comfy as M
    st = dict(C.settings())
    st.pop("llm_key", None)
    lines = [
        "===== Ascandir MusicBox – Debug-Bericht =====",
        f"Erstellt: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Version: {_version()}",
        f"Windows: {platform.platform()}",
        f"Python: {sys.version.split()[0]}  ({sys.executable})",
        f"Programmordner: {C.ROOT}",
        "",
        "----- Musik-KI (ComfyUI + ACE-Step 1.5) -----",
        f"Grafikkarten: {_safe(M.gpus)}",
        f"Erkannt: {_safe(M.gpu_vendor)}",
        f"ComfyUI-Ordner: {_safe(M.comfy_root)}",
        f"ComfyUI läuft: {_safe(M.running)}",
        f"Modelle: {_safe(M.model_status)}",
        "",
        "----- Sprach-KI (Strata) -----",
        f"Modus eingestellt: {st.get('ki_modus')}  →  tatsächlich: {_safe(L.resolved_mode)}",
        f"Strata gefunden: {_safe(lambda: L.find_strata(force=True))}",
        f"Strata-Startbefehl (Einstellung): {st.get('strata_befehl') or '(leer)'}",
        f"KI-Adresse: {st.get('llm_url')}  →  antwortet: {_safe(lambda: L.api_up(st['llm_url']))}",
        f"Prozesse am KI-Port: {_safe(lambda: _port_procs(L, st['llm_url']))}",
        f"strata-Befehl: {_safe(L.strata_cli)}",
        f"Strata-Preset (Einstellung): {st.get('strata_preset') or '(automatisch)'}  →  benutzt: {_safe(L.strata_preset)}",
        "strata launch --list-presets:",
        _safe(lambda: L.list_presets()[1]),
        "strata launch --status:",
        _safe(lambda: L.strata_status_urls()[1]),
        f"Laufende Strata-Prozesse: {_safe(_strata_procs)}",
        "",
        "----- Einstellungen -----",
        json.dumps(st, ensure_ascii=False, indent=2),
        "",
        "----- Programm-Protokoll (letzte 300 Zeilen) -----",
        _tail(LOGFILE, 300),
        "",
        "----- Musik-KI-Protokoll (letzte 200 Zeilen) -----",
        _tail(C.DATA / "comfy-log.txt", 200),
        "",
        "----- Sprach-KI-Protokoll (letzte 120 Zeilen) -----",
        _tail(C.DATA / "ki-log.txt", 120),
        "",
        "----- Start-Protokoll (letzte 80 Zeilen) -----",
        _tail(C.DATA / "log.txt", 80),
    ]
    return "\n".join(str(x) for x in lines)


def _port_procs(L, url):
    import psutil
    out = []
    for pid in L._listener_pids(L._port(url)):
        try:
            p = psutil.Process(pid)
            out.append(f"{pid} {p.name()} {p.exe()}")
        except Exception:
            out.append(str(pid))
    return out or "keine"


def _strata_procs():
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            text = " ".join([p.info["name"] or "", p.info["exe"] or "", " ".join(p.info["cmdline"] or [])])
            if "strata" in text.lower() and "musicbox" not in text.lower():
                out.append(f"{p.info['pid']} {p.info['name']} {p.info['exe']}")
        except Exception:
            pass
    return out or "keine"


def save_report():
    import re
    text = report()
    # API-Schlüssel nie mitschicken
    text = re.sub(r"(?i)(key[^:=\n]*[:=]\s*\"?)([A-Za-z0-9_\-\.]{4})[A-Za-z0-9_\-\.]{4,}", r"\1\2…", text)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M")
    folder = C.DATA / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"MusicBox-Debug-{stamp}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p), text
