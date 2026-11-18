"""Pfade und Einstellungen. Alles liegt im Ordner des Tools – nichts in der Cloud."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # .../Ascandir-MusicBox
DATA = ROOT / "daten"
MODELS = DATA / "modelle"
SONGS = ROOT / "Songs"
for p in (DATA, MODELS, SONGS):
    p.mkdir(parents=True, exist_ok=True)

APP_PORT = 7862          # Oberfläche (SessionRecorder nutzt 7860)
COMFY_PORT = 8193        # Musik-KI (ComfyUI)

DEFAULT_SETTINGS = {
    # Sprach-KI (Strata) – für Songtext und Berater
    "llm_url": "http://127.0.0.1:8080/v1",   # Strata-Standard
    "llm_model": "",                          # leer = automatisch erstes Modell
    "llm_key": "lokal",
    "ki_modus": "Automatisch",
    "strata_befehl": "",
    "strata_preset": "",
    "strata_beenden": True,
    "strata_wartezeit": 900,
    "berater_leerlauf_min": 10,               # Berater-KI nach so vielen Minuten Ruhe beenden
    # Musik-KI (ComfyUI + ACE-Step 1.5)
    "comfy_ordner": "",                       # leer = eingebaute Installation unter daten/comfyui
    "grafikkarte": "Automatisch",             # Automatisch | AMD | NVIDIA
    "gpu_modus": "Ausgewogen",                # Schonend | Ausgewogen | Volle Leistung
    "planer_modell": "1.7b",                  # 0.6b | 1.7b | 4b
    "audio_codes": True,                      # Song-Planer (LM) einschalten – bessere Qualität
    "musik_leerlauf_min": 10,                 # Musik-KI nach so vielen Minuten Ruhe beenden
    # Allgemein
    "design": "Dunkel",
    "update_repo": "Ascandir/Ascandir---MusicBox",
    "update_beim_start": True,
    "songs_ordner": str(SONGS),
}


def _path(name):
    return DATA / f"{name}.json"


def load(name, default):
    p = _path(name)
    data = dict(default) if isinstance(default, dict) else default
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(default, dict):
                data.update(stored)
            else:
                data = stored
        except Exception:
            pass
    return data


def save(name, data):
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def settings():
    st = load("einstellungen", DEFAULT_SETTINGS)
    if not st.get("ki_modus"):
        st["ki_modus"] = "Automatisch"
    return st


def update_settings(**kw):
    st = settings()
    st.update(kw)
    save("einstellungen", st)
    return st


def songs_dir():
    p = Path(settings().get("songs_ordner") or SONGS)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = SONGS
    return p
