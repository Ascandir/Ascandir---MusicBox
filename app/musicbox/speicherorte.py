"""Alles, was MusicBox und die Musik-KI zwischenspeichern, landet im Tool-Ordner (daten\\cache, daten\\tmp) –
nichts mehr auf der Systempartition. Muss vor dem Laden von Gradio/Torch aufgerufen werden.
Die Umgebungsvariablen erben auch ComfyUI und alle anderen Unterprozesse."""
import os
import shutil
import time
from pathlib import Path

from . import config as C

CACHE = C.DATA / "cache"
TMP = C.DATA / "tmp"


def umleiten():
    dirs = {
        "TEMP": TMP, "TMP": TMP, "TMPDIR": TMP,
        "GRADIO_TEMP_DIR": TMP / "gradio",                      # Gradio kopiert jeden Song hierhin
        "HF_HOME": CACHE / "huggingface", "TORCH_HOME": CACHE / "torch",
        "XDG_CACHE_HOME": CACHE, "PIP_CACHE_DIR": CACHE / "pip", "UV_CACHE_DIR": C.ROOT / "cache",
        "MIOPEN_USER_DB_PATH": CACHE / "miopen", "MIOPEN_CUSTOM_CACHE_DIR": CACHE / "miopen",   # AMD-Kernel-Cache
        "AMD_COMGR_CACHE_DIR": CACHE / "comgr", "TRITON_CACHE_DIR": CACHE / "triton",
        "CUDA_CACHE_PATH": CACHE / "nvidia",                    # NVIDIA-Kernel-Cache
        "TORCHINDUCTOR_CACHE_DIR": CACHE / "inductor",
    }
    for key, path in dirs.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    import tempfile
    tempfile.tempdir = str(TMP)
    _alte_temp_dateien_weg()


def _alte_temp_dateien_weg(tage=2):
    """Temp-Dateien älter als 2 Tage löschen (Kopien abgespielter Songs usw.)."""
    grenze = time.time() - tage * 86400
    for p in TMP.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < grenze:
                p.unlink()
        except Exception:
            pass


# ---------- Altlasten auf C: ----------
def _altlasten():
    """Zwischenspeicher, die frühere Versionen auf der Systempartition angelegt haben (alle neu erzeugbar)."""
    home = Path(os.path.expanduser("~"))
    win_temp = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Temp"
    return {
        "Gradio-Kopien abgespielter Songs": win_temp / "gradio",
        "AMD-Kernel-Cache (MIOpen)": home / ".miopen",
        "AMD-Kernel-Cache (MIOpen, AppData)": Path(os.environ.get("LOCALAPPDATA", "")) / "miopen",
        "PyTorch-Cache": home / ".cache" / "torch",
    }


def _groesse(p):
    total = 0
    try:
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except Exception:
                pass
    except Exception:
        pass
    return total


def altlasten_bericht():
    zeilen, summe = [], 0
    for name, p in _altlasten().items():
        if p.exists() and str(p) and not str(p).startswith(str(C.ROOT)):
            g = _groesse(p)
            summe += g
            if g > 1e6:
                zeilen.append(f"- {name}: **{g / 1e9:.2f} GB** – `{p}`")
    if not zeilen:
        return "✅ Keine Zwischenspeicher von MusicBox auf der Systempartition gefunden."
    return (f"Auf der Systempartition liegen noch **{summe / 1e9:.2f} GB** Zwischenspeicher (alle gefahrlos löschbar, "
            "werden bei Bedarf neu – jetzt im Tool-Ordner – angelegt):\n" + "\n".join(zeilen))


def altlasten_loeschen():
    frei = 0
    for name, p in _altlasten().items():
        if p.exists() and not str(p).startswith(str(C.ROOT)):
            frei += _groesse(p)
            shutil.rmtree(p, ignore_errors=True)
    return frei
