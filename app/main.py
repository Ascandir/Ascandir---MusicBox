import os
import sys

# Nichts nach außen senden
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from musicbox import config  # noqa: E402

# Ohne Konsolenfenster (pythonw) Ausgaben in eine Logdatei schreiben
if sys.stdout is None or sys.stderr is None:
    _log = open(config.DATA / "log.txt", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

from musicbox import debuglog  # noqa: E402
debuglog.setup()

# Alle Unterprozesse (ComfyUI, Strata …) enden automatisch mit diesem Python-Prozess – auch bei einem Absturz
try:
    from musicbox import jobobject  # noqa: E402
    debuglog.log.info("Prozess-Kopplung (Job): %s", "aktiv" if jobobject.enable() else "nicht verfügbar")
except Exception as _e:
    debuglog.log.info("Prozess-Kopplung (Job) fehlgeschlagen: %s", _e)


def einrichten():
    """Vom Setup aufgerufen: Musik-KI (ComfyUI) und Modelle laden. Gibt Fortschritt zeilenweise aus."""
    import time
    from musicbox import comfy
    last = {"t": 0.0, "m": ""}

    def progress(f, m):
        key = (m or "").split(" … ")[0]
        if key != last["m"] or time.time() - last["t"] > 5:
            last.update(t=time.time(), m=key)
            print(f"[{int((f or 0) * 100):3d} %] {m}", flush=True)
    print(f"Grafikkarte: {comfy._main_gpu()} → {comfy.gpu_vendor().upper()}", flush=True)
    hint = comfy.driver_hint()
    if hint:
        print(hint, flush=True)
    comfy.install(progress)
    print("Musik-KI ist eingerichtet.", flush=True)


if __name__ == "__main__":
    if "--einrichten" in sys.argv:
        try:
            einrichten()
        except Exception as e:
            print(f"FEHLER: {e}", flush=True)
            sys.exit(1)
    else:
        print("Ascandir - MusicBox startet ... (Fenster offen lassen, solange du das Tool benutzt)")
        from musicbox import ui
        ui.launch()
