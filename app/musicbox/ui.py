"""Bedienoberfläche (läuft nur auf deinem PC: http://127.0.0.1:7862) im eigenen Programmfenster."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import gradio as gr

from . import comfy as M
from . import config as C
from . import llm as L
from . import song as S
from . import texter as T
from . import updater as U
from .debuglog import log, save_report

MAX_VARIANTEN = 4
CATS = list(S.INSTRUMENTE)
CSS = """
footer {display: none !important;}
.status-card {border-left: 4px solid #f2a93b; background: rgba(242,169,59,.08);
              padding: 12px 16px !important; border-radius: 8px; min-height: 48px;}
.big-btn button, button.big-btn {font-size: 1.15em !important; min-height: 54px !important;}
h4 {margin-top: 14px !important; color: #f2a93b;}
.inst-group label {font-size: .95em;}
"""
WINDOW = None


# ---------- Song ↔ Regler ----------
def collect(*vals):
    """Werte aller Regler (Reihenfolge wie CONTROLS) → Song-Dict."""
    it = iter(vals)
    titel, stil, stil_en = next(it), next(it), next(it)
    inst = []
    for _ in CATS:
        inst += list(next(it) or [])
    gesang, sprache, idee, songtext, bpm, tonart, takt, dauer, varianten, qualitaet, schritte, stimmklang = it
    return S.clamp(dict(titel=titel, stil=stil, stil_en=stil_en, instrumente=inst, gesang=gesang, sprache=sprache,
                        idee=idee, songtext=songtext, bpm=bpm, tonart=tonart, takt=takt, dauer=dauer,
                        varianten=varianten, qualitaet=qualitaet, schritte=schritte, stimmklang=stimmklang))


def to_values(song):
    s = S.clamp(song)
    per_cat = [[t for _, t in S.INSTRUMENTE[c] if t in s["instrumente"]] for c in CATS]
    return [s["titel"], s["stil"], s["stil_en"], *per_cat, s["gesang"], s["sprache"], s["idee"], s["songtext"],
            s["bpm"], s["tonart"], s["takt"], s["dauer"], s["varianten"], s["qualitaet"], s["schritte"], s["stimmklang"]]


def save_draft(song):
    try:
        C.save("entwurf", song)
    except Exception:
        log.exception("Entwurf nicht gespeichert")


def load_draft():
    return S.clamp(C.load("entwurf", S.DEFAULT_SONG))


# ---------- Songtext ----------
def do_lyrics(*vals, progress=gr.Progress()):
    song = collect(*vals)
    if not S.mit_gesang(song):
        raise gr.Error("Bei „Kein Gesang“ braucht es keinen Songtext.")
    if not (song["idee"].strip() or song["stil"].strip()):
        raise gr.Error("Schreib zuerst eine grobe Idee oder beschreib die Musikrichtung.")
    try:
        titel, stil_en, text = T.write_lyrics(song, lambda f, m: progress(f, desc=m))
    except Exception as e:
        log.exception("Songtext fehlgeschlagen")
        raise gr.Error(f"Songtext fehlgeschlagen: {e}")
    song["songtext"] = text
    if stil_en:
        song["stil_en"] = stil_en
    if titel and not song["titel"].strip():
        song["titel"] = titel
    save_draft(song)
    beendet = ("Strata wurde danach wieder beendet." if not T.BERATER.active() and C.settings().get("strata_beenden", True)
               else "Strata läuft weiter (der Berater ist aktiv).")
    return [*to_values(song), f"✅ Songtext ist fertig – du kannst ihn unten noch frei bearbeiten. {beendet}"]


# ---------- Song erzeugen ----------
def _free_gpu_for_music():
    stopped = T.BERATER.stop()
    try:
        L.stop_all()
    except Exception:
        pass
    note = ""
    if L.api_up(C.settings()["llm_url"], timeout=1):
        note = ("\n\n⚠️ Strata läuft noch (nicht von MusicBox gestartet) und belegt Grafikspeicher. "
                "Wenn die Erzeugung abbricht: Strata vorher beenden.")
    return stopped, note


def do_generate(*vals, progress=gr.Progress()):
    *ctrl, zufall, seed = vals
    song = collect(*ctrl)
    save_draft(song)
    if S.mit_gesang(song) and not (song["songtext"].strip() or song["idee"].strip()):
        raise gr.Error("Es fehlt ein Text. Schreib eine Idee (und lass sie von Strata ausformulieren) "
                       "oder stell auf „Kein Gesang“.")
    if not (song["stil"].strip() or song["stil_en"].strip() or song["instrumente"]):
        raise gr.Error("Beschreib kurz die Musikrichtung oder wähl ein paar Instrumente.")
    seed = S.random_seed() if zufall or not seed else int(seed)
    if song["qualitaet"] == M.Q_EXPERIMENT:
        log.info("Beste Qualität: XL-SFT mit %s Schritten", song["schritte"])
    stopped, note = _free_gpu_for_music()
    if stopped:
        log.info("Berater-KI für die Musik beendet")
    p = {"tags": S.tags(song), "lyrics": S.lyrics(song), "bpm": song["bpm"], "dauer": song["dauer"],
         "takt": M.TAKTE[song["takt"]], "sprache": M.SPRACHEN.get(song["sprache"], "de"), "tonart": song["tonart"],
         "seed": seed, "varianten": song["varianten"], "titel": song["titel"] or "Song", "meta": song,
         "qualitaet": song["qualitaet"], "schritte": song["schritte"]}
    log.info("Song erzeugen: %s", json.dumps({k: v for k, v in p.items() if k != "meta"}, ensure_ascii=False)[:1500])
    t0 = time.time()

    def prog(f, m):
        progress(f if f is not None else None, desc=m)
    try:
        files = M.generate(p, prog)
    except Exception as e:
        log.exception("Song-Erzeugung fehlgeschlagen")
        if C.settings().get("musik_nach_song_beenden", True):
            M.stop()
        raise gr.Error(f"{e}", duration=None)
    sec = int(time.time() - t0)
    if C.settings().get("musik_nach_song_beenden", True) and M.stop():
        note += "\n\n⏹ Die Musik-KI wurde beendet – die Grafikkarte ist wieder frei."
    md = (f"✅ **Fertig** in {sec // 60}:{sec % 60:02d} min – gespeichert im Songs-Ordner. "
          f"Seed: `{seed}` (für dieselbe Variante nochmal: „Zufällig“ aus und Seed eintragen).{note}")
    ups = []
    for i in range(MAX_VARIANTEN):
        if i < len(files):
            ups.append(gr.update(value=files[i], visible=True, label=f"🎵 Neu: {Path(files[i]).stem}"))
        else:
            ups.append(gr.update(value=None, visible=False))
    return [md, seed, *ups]


def do_cancel():
    M.cancel()
    return "⏹ Abbruch angefordert …"


# ---------- Berater ----------
def do_chat(msg, history, *vals, progress=gr.Progress()):
    msg = (msg or "").strip()
    history = list(history or [])
    if not msg:
        return [history, "", *to_values(collect(*vals)), gr.update()]
    song = collect(*vals)
    history.append({"role": "user", "content": msg})
    try:
        text, new, changes = T.BERATER.ask(history, song, lambda f, m: progress(f, desc=m))
    except Exception as e:
        log.exception("Berater fehlgeschlagen")
        history.append({"role": "assistant", "content": f"❌ {e}"})
        return [history, msg, *to_values(song), berater_status()]
    if changes:
        text += "\n\n🎛️ **Eingestellt:** " + " · ".join(changes)
        save_draft(new)
    history.append({"role": "assistant", "content": text})
    return [history, "", *to_values(new), berater_status()]


def berater_status():
    mins = C.settings().get("berater_leerlauf_min", 10)
    if T.BERATER.active():
        return f"🟢 Strata läuft für den Berater – wird nach {mins} Min. Ruhe oder beim Song-Erzeugen beendet."
    if T.BERATER.url:
        return "🟢 Strata läuft (selbst gestartet – MusicBox beendet es nicht)."
    return "⚪ Strata ist aus – es startet bei deiner ersten Nachricht (das dauert etwas)."


def do_berater_stop():
    T.BERATER.stop()
    return berater_status()


# ---------- Meine Songs ----------
AUDIO_EXT = {".flac", ".mp3", ".wav", ".ogg", ".opus"}


def song_list():
    d = C.songs_dir()
    files = [p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT and p.is_file()] if d.exists() else []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [(p.stem, str(p)) for p in files[:300]]


def song_meta(path):
    p = Path(path).with_suffix(".json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def show_song(path):
    if not path or not Path(path).exists():
        return gr.update(value=None), "*Wähl einen Song aus.*"
    m = song_meta(path)
    if not m:
        return gr.update(value=path), "*Keine Angaben zu diesem Song gespeichert.*"
    inst = ", ".join(S.ALLE_INSTRUMENTE.get(t, t) for t in m.get("instrumente", [])) or "–"
    d = int(m.get("dauer", 0))
    md = (f"### {m.get('titel') or Path(path).stem}\n"
          f"**Stil:** {m.get('stil') or m.get('stil_en') or '–'}  \n"
          f"**Instrumente:** {inst}  \n**Gesang:** {m.get('gesang', '–')}"
          f"{' (' + m['stimmklang'] + ')' if m.get('stimmklang') and m.get('gesang') != S.INSTRUMENTAL else ''}"
          f" · **Sprache:** {m.get('sprache', '–')}  \n"
          f"**BPM:** {m.get('bpm')} · **Tonart:** {m.get('tonart')} · **Takt:** {m.get('takt')} · "
          f"**Länge:** {d // 60}:{d % 60:02d} · **Seed:** `{m.get('seed')}`  \n"
          f"*Erstellt {m.get('erstellt', '?')} in {m.get('dauer_erzeugung_s', '?')} s "
          f"({m.get('qualitaet') or m.get('musik_modell', '?')}"
          f"{', ' + str(m.get('schritte')) + ' Schritte' if m.get('qualitaet') == M.Q_EXPERIMENT else ''}).*")
    if m.get("songtext") and m.get("gesang") != S.INSTRUMENTAL:
        md += "\n\n**Songtext:**\n\n" + m["songtext"].replace("\n", "  \n")
    return gr.update(value=path), md


def use_song(path):
    m = song_meta(path) if path else {}
    if not m:
        raise gr.Error("Zu diesem Song sind keine Einstellungen gespeichert.")
    song = S.clamp(m)
    save_draft(song)
    return [*to_values(song), m.get("seed") or 0, False, gr.Tabs(selected="song")]


def delete_song(path):
    if path and Path(path).exists():
        Path(path).unlink()
        Path(path).with_suffix(".json").unlink(missing_ok=True)
    return gr.update(choices=song_list(), value=None), gr.update(value=None), "🗑️ Gelöscht."


def open_songs():
    if sys.platform == "win32":
        os.startfile(str(C.songs_dir()))
    return f"Ordner: `{C.songs_dir()}`"


# ---------- Einstellungen ----------
def musik_status():
    st = C.settings()
    g = M._main_gpu()
    lines = [f"**Grafikkarte:** {g['name'] if g else 'nicht erkannt'} → ComfyUI für "
             f"**{'NVIDIA' if M.gpu_vendor() == 'nvidia' else 'AMD (ROCm)'}**"]
    root = M.comfy_root()
    if root is None:
        lines.append("❌ Musik-KI (ComfyUI) ist noch nicht installiert – wird beim ersten Song automatisch geladen, "
                     "oder jetzt mit „Musik-KI installieren / Modelle laden“.")
    else:
        lines.append(f"✅ ComfyUI: `{root}`")
        for qn in M.QUALITAET:
            miss = M.missing_files(st, qn)
            lines.append(f"✅ {qn}: bereit" if not miss else
                         f"⬇️ {qn}: lädt beim ersten Song noch " +
                         ", ".join(f"{M.FILES[k]['name']} (~{M.FILES[k]['gb']:.1f} GB)" for k in miss))
    lines.append("🟢 Musik-KI läuft gerade." if M.running() else "⚪ Musik-KI ist aus (startet beim Erzeugen).")
    hint = M.driver_hint()
    if hint:
        lines.append(hint)
    return "  \n".join(lines)


def ki_status():
    st = C.settings()
    mode = L.resolved_mode()
    pre = f"**Automatisch gewählt: {mode}.** " if st.get("ki_modus", "Automatisch") == "Automatisch" else ""
    if mode == "Strata":
        d = L.strata_description()
        if L.strata_cli():
            p = st.get("strata_preset") or "(erstes gespeichertes)"
            d += f"  \nPreset: **{p}** – Start ohne Fenster, Stopp über `strata launch --stop`."
        return pre + d
    if mode == "Eingebaute KI":
        return pre + ("✅ Eingebaute KI ist installiert." if L.builtin_installed() else
                      "Die eingebaute KI wird beim ersten Bedarf automatisch geladen (ca. 9 GB).")
    return pre + f"Es wird die KI unter `{st['llm_url']}` genutzt. Sie muss selbst gestartet sein."


def do_install_music(progress=gr.Progress()):
    if M.BUSY.locked():
        raise gr.Error("Gerade wird ein Song erzeugt.")
    try:
        M.install(lambda f, m: progress(f, desc=m))
    except Exception as e:
        log.exception("Installation Musik-KI fehlgeschlagen")
        raise gr.Error(f"Installation fehlgeschlagen: {e}", duration=None)
    return musik_status()


def do_music_stop():
    if M.BUSY.locked():
        raise gr.Error("Gerade wird ein Song erzeugt – erst abbrechen.")
    M.stop()
    return musik_status()


def save_settings(planer, codes, gpu_modus, karte, mafter, musik_idle, comfy_ordner,
                  ki_mode, strata_cmd, strata_stop, strata_wait, berater_idle, url, model,
                  songs_ordner, design, upd_start):
    old = C.settings()
    st = C.update_settings(
        planer_modell=M.PLANER_MODELLE.get(planer, "1.7b"),
        audio_codes=bool(codes), gpu_modus=gpu_modus, grafikkarte=karte, musik_nach_song_beenden=bool(mafter),
        musik_leerlauf_min=int(musik_idle),
        comfy_ordner=(comfy_ordner or "").strip().strip('"'), ki_modus=ki_mode, strata_befehl=(strata_cmd or "").strip(),
        strata_beenden=bool(strata_stop), strata_wartezeit=int(strata_wait) * 60, berater_leerlauf_min=int(berater_idle),
        llm_url=(url or old["llm_url"]).strip(), llm_model=(model or "").strip(),
        songs_ordner=(songs_ordner or str(C.SONGS)).strip(), design=design, update_beim_start=bool(upd_start))
    M._gpu_cache.clear()
    L.find_strata(force=True)
    restart = ""
    if M.started_by_us() and (old.get("gpu_modus") != st["gpu_modus"] or old.get("comfy_ordner") != st["comfy_ordner"]):
        M.stop()
        restart = " Die Musik-KI wurde beendet und startet beim nächsten Song mit den neuen Einstellungen."
    return f"✅ Gespeichert.{restart}", musik_status(), ki_status()


def test_llm(progress=gr.Progress()):
    try:
        T._free_gpu_for_strata()
        ans = L.test(lambda f, m: progress(f, desc=m))
        return f"✅ {ans}\n\nDie KI wurde danach wieder beendet (falls MusicBox sie gestartet hat)."
    except Exception as e:
        log.exception("KI-Test fehlgeschlagen")
        return f"❌ {e}\n\nKlick auf **🐞 Debug-Log erstellen** und schick mir die Datei."


def do_find_strata():
    L.find_strata(force=True)
    names, raw = L.list_presets()
    cur = C.settings().get("strata_preset", "")
    return (ki_status(), gr.update(choices=["(automatisch)"] + names, value=cur if cur in names else "(automatisch)"),
            f"```\n{raw or 'keine Ausgabe'}\n```")


def save_preset(name):
    C.update_settings(strata_preset="" if name in (None, "(automatisch)") else name)
    return ki_status()


def pick_strata():
    if WINDOW is None:
        raise gr.Error("Die Dateiauswahl gibt es nur im Programmfenster – trag den Pfad bitte ins Feld ein.")
    import webview
    res = WINDOW.create_file_dialog(webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG,
                                    file_types=("Programme (*.exe;*.bat;*.cmd)", "Alle Dateien (*.*)"))
    if not res:
        return gr.update(), ki_status()
    path = res[0] if isinstance(res, (list, tuple)) else res
    C.update_settings(strata_befehl=path)
    return gr.update(value=path), ki_status()


def do_debug():
    try:
        path, text = save_report()
    except Exception as e:
        raise gr.Error(f"Debug-Log konnte nicht erstellt werden: {e}")
    return ("✅ Erstellt – lade die Datei unten herunter oder kopier den Text. (Liegt auch unter `daten\\debug`.)",
            gr.update(value=text, visible=True), gr.update(value=path, visible=True))


# ---------- Updates & Beenden ----------
def update_text(info):
    if "fehler" in info:
        return f"Du hast Version **{U.VERSION}**. Update-Prüfung: {info['fehler']}", gr.update(visible=False)
    if info.get("neu"):
        txt = f"### ⬆️ Neue Version {info['version']} verfügbar (du hast {U.VERSION})"
        if info.get("text"):
            txt += "\n\n" + info["text"]
        return txt, gr.update(visible=True)
    return f"✅ Du hast die neueste Version (**{U.VERSION}**).", gr.update(visible=False)


def do_update(progress=gr.Progress()):
    if M.BUSY.locked():
        raise gr.Error("Erst warten, bis der Song fertig ist.")
    # Vor dem Update alle von MusicBox gestarteten KIs beenden – sonst laufen sie verwaist weiter
    for fn in (T.BERATER.stop, L.stop_all, M.stop):
        try:
            fn()
        except Exception:
            log.exception("KI vor dem Update nicht beendet")
    try:
        v = U.install(progress=lambda f, m: progress(f, desc=m))
    except Exception as e:
        raise gr.Error(f"Update fehlgeschlagen: {e}")
    return (f"⬆️ Version {v} wird installiert. MusicBox schließt sich jetzt und startet nach dem Update neu."), \
        gr.update(visible=False)


def shutdown():
    """Beendet alles: gestartete KIs, Unterprozesse, Python."""
    for fn in (M.cancel, T.BERATER.stop, L.stop_all, M.stop):
        try:
            fn()
        except Exception:
            pass
    try:
        import psutil
        kids = psutil.Process().children(recursive=True)
        for k in kids:
            try:
                k.terminate()
            except Exception:
                pass
        psutil.wait_procs(kids, timeout=3)
        for k in kids:
            try:
                k.kill()
            except Exception:
                pass
    except Exception:
        pass
    os._exit(0)


def close_app():
    if WINDOW is not None:
        try:
            WINDOW.destroy()
            return
        except Exception:
            pass
    shutdown()


def do_quit():
    threading.Timer(0.5, close_app).start()
    return "👋 MusicBox wird beendet …"


_LAST_LOAD = [0.0]


def _on_load():
    _LAST_LOAD[0] = time.time()


def _on_unload():
    """Nur ohne eigenes Fenster (Browser-Notlösung): Tab zu → nach 20 s alles beenden, außer er wurde neu geladen."""
    if WINDOW is not None:
        return
    t = time.time()

    def check():
        if _LAST_LOAD[0] < t and not M.BUSY.locked():
            log.info("Browser-Tab geschlossen – MusicBox wird beendet")
            shutdown()
    threading.Timer(20, check).start()


def _ticker():
    while True:
        time.sleep(30)
        for fn in (M.idle_check, T.BERATER.idle_check):
            try:
                fn()
            except Exception:
                log.exception("Leerlauf-Prüfung")


# ---------- Oberfläche ----------
def build():
    st = C.settings()
    draft = load_draft()
    vals = to_values(draft)
    theme = gr.themes.Soft(primary_hue="amber", neutral_hue="stone",
                           font=["Segoe UI", "system-ui", "sans-serif"],
                           font_mono=["Consolas", "monospace"]).set(
        body_background_fill_dark="#15110e",
        block_background_fill_dark="#211b16",
        input_background_fill_dark="#2a231d",
        border_color_primary_dark="#3a3027",
    )
    with gr.Blocks(title="Ascandir - MusicBox") as app:
        with gr.Row():
            gr.Markdown(f"# 🎵 Ascandir · MusicBox <sup style='font-size:.45em;opacity:.6'>v{U.VERSION}</sup>\n"
                        "Songs mit lokaler KI bauen – alles bleibt auf deinem PC.")
            upd_btn = gr.Button("⬆️ Updates", scale=0, min_width=110, size="sm")
            quit_btn = gr.Button("⏻ Beenden", scale=0, min_width=110, size="sm")
        quit_md = gr.Markdown()
        upd_md = gr.Markdown()
        upd_go = gr.Button("⬆️ Jetzt aktualisieren", variant="primary", visible=False)

        with gr.Tabs() as tabs:
            # ===== Song bauen =====
            with gr.Tab("🎼 Song bauen", id="song"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3):
                        titel = gr.Textbox(label="Titel", value=vals[0], placeholder="z. B. Die Taverne am Rand der Welt")
                        stil = gr.Textbox(label="Musikrichtung & Feeling", value=vals[1], lines=3,
                                          placeholder="z. B. düsterer Fantasy-Folk, beginnt ruhig wie eine Taverne bei Nacht, "
                                                      "baut sich auf, Refrain episch und hymnisch")
                        with gr.Accordion("🔤 Stil für die Musik-KI (englisch, wird von Strata ausgefüllt)", open=False):
                            stil_en = gr.Textbox(show_label=False, value=vals[2], lines=2,
                                                 placeholder="dark fantasy folk, slow build, tavern atmosphere, epic anthemic chorus",
                                                 info="Die Musik-KI versteht Englisch am besten. Leer = es wird dein Text oben benutzt.")
                        with gr.Accordion("🎻 Instrumente", open=True):
                            inst_groups = []
                            for i, cat in enumerate(CATS):
                                inst_groups.append(gr.CheckboxGroup(
                                    choices=[(lab, tag) for lab, tag in S.INSTRUMENTE[cat]], value=vals[3 + i],
                                    label=cat, elem_classes=["inst-group"]))
                        gesang = gr.Radio(list(S.GESANG), value=vals[3 + len(CATS)], label="🎤 Gesang / Lyrics")
                        with gr.Group(visible=draft["gesang"] != S.INSTRUMENTAL) as lyr_box:
                            stimmklang = gr.Radio(list(S.STIMMKLANG), value=vals[14 + len(CATS)], label="🎙️ Klang der Stimme",
                                                  info="Natürlich = möglichst echte, unbearbeitete Stimme · Autotune = der typische "
                                                       "„Roboter“-Effekt aus Pop/Trap")
                            sprache = gr.Dropdown(list(M.SPRACHEN), value=vals[4 + len(CATS)], label="Sprache des Songtexts")
                            idee = gr.Textbox(label="Deine Idee (grober Text)", value=vals[5 + len(CATS)], lines=5,
                                              placeholder="Schreib einfach drauf los: worum geht's, Stichpunkte, Zeilen, die "
                                                          "unbedingt rein sollen …")
                            lyr_btn = gr.Button("✨ Mit Strata zum richtigen Songtext machen", variant="secondary")
                            lyr_md = gr.Markdown("Strata wird dafür gestartet und danach automatisch wieder beendet.")
                            songtext = gr.Textbox(label="Songtext (fertig – frei bearbeitbar)", value=vals[6 + len(CATS)],
                                                  lines=16, buttons=["copy"],
                                                  placeholder="[Verse 1]\n…\n\n[Chorus]\n…")
                    with gr.Column(scale=2):
                        gr.Markdown("#### 🎚️ Musik")
                        bpm = gr.Slider(S.BPM_MIN, S.BPM_MAX, value=vals[7 + len(CATS)], step=1, label="Tempo (BPM)")
                        tonart = gr.Dropdown(M.TONARTEN, value=vals[8 + len(CATS)], label="Tonart",
                                             info="minor = Moll (eher traurig/düster), major = Dur (eher fröhlich)")
                        takt = gr.Radio(list(M.TAKTE), value=vals[9 + len(CATS)], label="Takt")
                        dauer = gr.Slider(S.DAUER_MIN, S.DAUER_MAX, value=vals[10 + len(CATS)], step=5,
                                          label="Länge (Sekunden)", info="150 s = 2:30 min")
                        varianten = gr.Slider(1, MAX_VARIANTEN, value=vals[11 + len(CATS)], step=1,
                                              label="Varianten", info="Mehrere Versionen nacheinander (jede mit anderem Seed)")
                        qualitaet = gr.Radio(list(M.QUALITAET), value=vals[12 + len(CATS)], label="💎 Qualität",
                                             info="Beim ersten Mal wird das jeweilige Modell nachgeladen (XL ca. 9,5 GB, großer Song-Planer ca. 8 GB). "
                                                  "„Beste“ plant den Song gründlicher und braucht dafür länger.")
                        schritte = gr.Slider(20, 100, value=vals[13 + len(CATS)], step=5, label="Feinschliff (Rechenschritte)",
                                             info="Nur bei „Experimentell“: 50 = empfohlen. Mehr Schritte = feinere Details, dauert länger.",
                                             visible=draft["qualitaet"] == M.Q_EXPERIMENT)
                        with gr.Row():
                            zufall = gr.Checkbox(value=True, label="Zufälliger Seed", scale=1)
                            seed = gr.Number(value=0, precision=0, label="Seed", scale=2)
                        gen_btn = gr.Button("🎵 Song erzeugen", variant="primary", elem_classes=["big-btn"])
                        stop_btn = gr.Button("⏹ Abbrechen", variant="stop", size="sm")
                        gen_md = gr.Markdown("Beim ersten Mal werden die Musik-KI und ihre Modelle geladen.",
                                             elem_classes=["status-card"])
                        outs = [gr.Audio(type="filepath", interactive=False, visible=False, buttons=["download"])
                                for _ in range(MAX_VARIANTEN)]
                        with gr.Row():
                            folder_btn = gr.Button("📂 Songs-Ordner öffnen", size="sm")
                        folder_md = gr.Markdown()

            # ===== KI-Berater =====
            with gr.Tab("💬 KI-Berater", id="berater"):
                gr.Markdown("Besprich deinen Song mit Strata – Stil, Tempo, Instrumente, Text. Der Berater sieht die "
                            "aktuellen Regler und **stellt sie direkt ein**. Die Änderungen siehst du sofort im Tab "
                            "„🎼 Song bauen“.")
                chat = gr.Chatbot(height=520, label="KI-Berater", buttons=["copy"],
                                  placeholder="z. B. *„Ich will ein Lied über unsere D&D-Gruppe, so Taverne-mitgrölen, "
                                              "eher schnell. Was schlägst du vor?“*")
                with gr.Row():
                    chat_in = gr.Textbox(show_label=False, placeholder="Nachricht an den Berater …", scale=6, lines=2)
                    chat_go = gr.Button("Senden", variant="primary", scale=1)
                with gr.Row():
                    chat_new = gr.Button("🔄 Neues Gespräch", size="sm")
                    chat_stop = gr.Button("⏹ Berater beenden (Strata aus)", size="sm")
                    to_song = gr.Button("🎼 Zum Song", size="sm")
                chat_md = gr.Markdown(berater_status())

            # ===== Meine Songs =====
            with gr.Tab("🎧 Meine Songs", id="songs"):
                with gr.Row():
                    lib = gr.Dropdown(song_list(), label="Song", scale=6)
                    lib_ref = gr.Button("🔄", scale=0, min_width=50)
                lib_audio = gr.Audio(type="filepath", interactive=False, buttons=["download"])
                with gr.Row():
                    lib_use = gr.Button("⤴️ Einstellungen übernehmen (zum Weiterbauen)", variant="secondary")
                    lib_folder = gr.Button("📂 Ordner öffnen")
                    lib_del = gr.Button("🗑️ Löschen", variant="stop")
                lib_md = gr.Markdown("*Wähl einen Song aus.*")

            # ===== Einstellungen =====
            with gr.Tab("⚙️ Einstellungen", id="einstellungen"):
                with gr.Row():
                    dbg_btn = gr.Button("🐞 Debug-Log erstellen", scale=0, min_width=220)
                    dbg_md = gr.Markdown("Sammelt alle Infos zur Fehlersuche in einer Datei zum Verschicken.")
                dbg_file = gr.File(label="Debug-Log", visible=False, interactive=False)
                dbg_text = gr.Textbox(label="Inhalt", lines=12, visible=False, buttons=["copy"])

                gr.Markdown("### 🎵 Musik-KI (ACE-Step 1.5 über ComfyUI)")
                mus_md = gr.Markdown(musik_status())
                rev_p = {v: k for k, v in M.PLANER_MODELLE.items()}
                with gr.Row():
                    s_planer = gr.Radio(list(M.PLANER_MODELLE), value=rev_p.get(st.get("planer_modell"), list(M.PLANER_MODELLE)[0]),
                                        label="Song-Planer (für Schnell & Besser)",
                                        info="Plant Aufbau, Melodie und Gesang, bevor die Musik entsteht. „Beste“ und „Experimentell“ nehmen immer den starken.")
                s_codes = gr.Checkbox(value=st.get("audio_codes", True),
                                      label="Song-Planer benutzen (langsamer, aber deutlich bessere Songs – empfohlen)")
                with gr.Row():
                    s_gpu = gr.Radio(list(M.GPU_FLAGS), value=st.get("gpu_modus", "Ausgewogen"), label="Grafikkarten-Modus",
                                     info="Schonend, falls der AMD-Treiber abstürzt oder der Speicher nicht reicht")
                    s_karte = gr.Radio(["Automatisch", "AMD", "NVIDIA"], value=st.get("grafikkarte", "Automatisch"),
                                       label="Grafikkarte", info="Welche ComfyUI-Version geladen wird")
                s_mafter = gr.Checkbox(value=st.get("musik_nach_song_beenden", True),
                                       label="Musik-KI direkt nach jedem Song beenden (empfohlen – gibt die Grafikkarte frei; "
                                             "der nächste Song startet dafür etwas langsamer)")
                s_midle = gr.Slider(0, 60, value=st.get("musik_leerlauf_min", 10), step=1,
                                    label="Falls nicht direkt beendet: nach so vielen Minuten ohne Benutzung beenden (0 = nie)")
                s_comfy = gr.Textbox(label="Eigener ComfyUI-Ordner (leer = eingebaute Installation)",
                                     value=st.get("comfy_ordner", ""),
                                     placeholder=r"z. B. D:\KI\ComfyUI_windows_portable (Ordner mit python_embeded)")
                with gr.Row():
                    inst_btn = gr.Button("⬇️ Musik-KI installieren / fehlende Modelle laden")
                    mstop_btn = gr.Button("⏹ Musik-KI jetzt beenden")
                    mref_btn = gr.Button("🔄 Status")

                gr.Markdown("### 🧠 Sprach-KI (Strata) – für Songtext & Berater")
                s_kimode = gr.Radio(L.MODI, value=st.get("ki_modus", "Automatisch"), label="Welche KI?",
                                    info="Automatisch: Strata, wenn es da ist – sonst die eingebaute KI.")
                ki_md = gr.Markdown(ki_status())
                with gr.Accordion("Strata-Einstellungen", open=False):
                    s_scmd = gr.Textbox(label="Strata-Startbefehl oder Pfad (leer = automatisch suchen)",
                                        value=st.get("strata_befehl", ""),
                                        placeholder=r"z. B. C:\Users\Tim\AppData\Local\Programs\Strata\Strata.exe")
                    with gr.Row():
                        s_preset = gr.Dropdown(["(automatisch)"], value="(automatisch)", label="Strata-Preset", scale=4)
                        s_find = gr.Button("🔍 Strata & Presets suchen", scale=1)
                    presets_raw = gr.Markdown()
                    s_pick = gr.Button("📂 Strata-Programm selbst auswählen …")
                    s_sstop = gr.Checkbox(value=st.get("strata_beenden", True),
                                          label="Strata nach dem Songtext wieder beenden (gibt die Grafikkarte frei)")
                    s_swait = gr.Slider(2, 30, value=int(st.get("strata_wartezeit", 900)) // 60, step=1,
                                        label="So lange auf Strata warten (Minuten)")
                    s_bidle = gr.Slider(1, 60, value=st.get("berater_leerlauf_min", 10), step=1,
                                        label="Berater: Strata nach so vielen Minuten Ruhe beenden")
                with gr.Accordion("Eigene Adresse / Experten", open=False):
                    s_url = gr.Textbox(label="Adresse der Sprach-KI", value=st["llm_url"], info="Strata: http://127.0.0.1:8080/v1")
                    s_model = gr.Textbox(label="Modellname (leer = automatisch)", value=st["llm_model"])

                gr.Markdown("### Allgemein")
                s_songs = gr.Textbox(label="Ordner für deine Songs", value=st.get("songs_ordner", str(C.SONGS)))
                s_design = gr.Radio(["Dunkel", "Hell"], value=st.get("design", "Dunkel"), label="Design (nach Neustart)")
                s_upd = gr.Checkbox(value=st.get("update_beim_start", True),
                                    label="Beim Start nach Updates suchen (fragt nur die Versionsnummer bei GitHub ab)")
                with gr.Row():
                    s_save = gr.Button("Speichern", variant="primary")
                    s_test = gr.Button("Sprach-KI testen (starten → fragen → beenden)")
                s_md = gr.Markdown()

        # ---------- Verdrahtung ----------
        CONTROLS = [titel, stil, stil_en, *inst_groups, gesang, sprache, idee, songtext, bpm, tonart, takt, dauer,
                    varianten, qualitaet, schritte, stimmklang]

        quit_btn.click(do_quit, None, quit_md)
        upd_btn.click(lambda: update_text(U.check(force=True)), None, [upd_md, upd_go])
        upd_go.click(do_update, None, [upd_md, upd_go])

        def startup():
            info = U.check() if C.settings().get("update_beim_start", True) else {}
            up = update_text(info) if info.get("neu") else ("", gr.update(visible=False))
            d = load_draft()
            return [*up, *to_values(d), gr.update(visible=d["gesang"] != S.INSTRUMENTAL),
                    gr.update(visible=d["qualitaet"] == M.Q_EXPERIMENT)]
        app.load(startup, None, [upd_md, upd_go, *CONTROLS, lyr_box, schritte])
        app.load(_on_load, None, None)
        app.unload(_on_unload)

        gesang.change(lambda g: gr.update(visible=g != S.INSTRUMENTAL), gesang, lyr_box)
        qualitaet.change(lambda q: gr.update(visible=q == M.Q_EXPERIMENT), qualitaet, schritte)
        lyr_btn.click(do_lyrics, CONTROLS, [*CONTROLS, lyr_md], concurrency_limit=1, show_progress_on=[lyr_md])
        # Erst die alten Player leeren und stoppen (sonst bleibt im Programmfenster evtl. der alte Song stehen),
        # dann erzeugen
        ev_gen = gen_btn.click(
            lambda: ["⏳ Song wird vorbereitet …", *[gr.update(value=None, visible=False) for _ in outs]],
            None, [gen_md, *outs], queue=False).then(
            do_generate, [*CONTROLS, zufall, seed], [gen_md, seed, *outs],
            concurrency_limit=1, show_progress_on=[gen_md])
        ev_gen.then(lambda: gr.update(choices=song_list()), None, lib)
        stop_btn.click(do_cancel, None, gen_md)
        folder_btn.click(open_songs, None, folder_md)

        chat_args = dict(fn=do_chat, inputs=[chat_in, chat, *CONTROLS], outputs=[chat, chat_in, *CONTROLS, chat_md],
                         concurrency_limit=1, show_progress_on=[chat_md])
        chat_go.click(**chat_args)
        chat_in.submit(**chat_args)
        chat_new.click(lambda: ([], berater_status()), None, [chat, chat_md])
        chat_stop.click(do_berater_stop, None, chat_md)
        to_song.click(lambda: gr.Tabs(selected="song"), None, tabs)

        lib_ref.click(lambda: gr.update(choices=song_list()), None, lib)
        lib.change(show_song, lib, [lib_audio, lib_md])
        lib_use.click(use_song, lib, [*CONTROLS, seed, zufall, tabs])
        lib_folder.click(open_songs, None, lib_md)
        lib_del.click(delete_song, lib, [lib, lib_audio, lib_md])

        dbg_btn.click(do_debug, None, [dbg_md, dbg_text, dbg_file])
        inst_btn.click(do_install_music, None, mus_md, concurrency_limit=1)
        mstop_btn.click(do_music_stop, None, mus_md)
        mref_btn.click(musik_status, None, mus_md)
        s_save.click(save_settings, [s_planer, s_codes, s_gpu, s_karte, s_mafter, s_midle, s_comfy, s_kimode, s_scmd,
                                     s_sstop, s_swait, s_bidle, s_url, s_model, s_songs, s_design, s_upd],
                     [s_md, mus_md, ki_md])
        s_kimode.change(lambda m: (C.update_settings(ki_modus=m), ki_status())[1], s_kimode, ki_md)
        s_find.click(do_find_strata, None, [ki_md, s_preset, presets_raw])
        s_preset.input(save_preset, s_preset, ki_md)
        s_pick.click(pick_strata, None, [s_scmd, ki_md])
        s_test.click(test_llm, None, s_md, concurrency_limit=1)
    return app, theme


def _open_window(url):
    """Eigenes Programmfenster (WebView2). Schließen = alles beenden."""
    global WINDOW
    import webview
    WINDOW = webview.create_window("Ascandir - MusicBox", url, width=1400, height=940, min_size=(1000, 700),
                                   background_color="#15110e", text_select=True)

    def on_closing():
        if M.BUSY.locked():
            return WINDOW.create_confirmation_dialog("Song wird erzeugt",
                                                     "Gerade entsteht ein Song.\nAbbrechen und MusicBox schließen?")
        return True
    WINDOW.events.closing += on_closing
    webview.start(private_mode=False, storage_path=str(C.DATA / "fenster"))


def launch(open_window=True):
    import socket
    import webbrowser
    dark = C.settings().get("design", "Dunkel") == "Dunkel"
    url = f"http://127.0.0.1:{C.APP_PORT}/" + ("?__theme=dark" if dark else "?__theme=light")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", C.APP_PORT)) == 0:
            print("MusicBox läuft schon – öffne ein Fenster dazu.")
            try:
                _open_window(url)
            except Exception:
                webbrowser.open(url)
            return
    app, theme = build()
    threading.Thread(target=_ticker, daemon=True).start()
    js = ("() => { document.body.classList.add('dark'); }" if dark
          else "() => { document.body.classList.remove('dark'); }")
    app.queue(default_concurrency_limit=4).launch(
        server_name="127.0.0.1", server_port=C.APP_PORT, inbrowser=False, prevent_thread_lock=True,
        theme=theme, js=js, show_error=True, quiet=True, css=CSS,
        allowed_paths=[str(C.songs_dir()), str(C.SONGS), str(C.DATA)])
    if not open_window:
        return app
    try:
        _open_window(url)
    except Exception as e:      # kein WebView2 → Browser
        print("Fenster nicht möglich, öffne Browser:", e)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    shutdown()
