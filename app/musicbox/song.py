"""Was einen Song ausmacht: Instrumente, Gesang, Regler – und wie daraus die Beschreibung für die Musik-KI wird."""
import random
import re

from .comfy import SPRACHEN, TAKTE, TONARTEN

# Kategorie → [(Anzeige, englischer Begriff für die Musik-KI)]
INSTRUMENTE = {
    "🥁 Schlagzeug & Rhythmus": [
        ("Schlagzeug", "drums"), ("Drumcomputer", "drum machine"), ("808-Bass", "808 bass"),
        ("Percussion", "percussion"), ("Handclaps", "handclaps"), ("Breakbeat", "breakbeat"),
        ("Taiko-Trommeln", "taiko drums"), ("Pauken", "timpani"),
    ],
    "🎸 Saiten": [
        ("Akustikgitarre", "acoustic guitar"), ("E-Gitarre (clean)", "clean electric guitar"),
        ("E-Gitarre (verzerrt)", "distorted electric guitar"), ("Bassgitarre", "bass guitar"),
        ("Synth-Bass", "synth bass"), ("Kontrabass", "upright bass"), ("Geige", "violin"), ("Cello", "cello"),
        ("Streicher-Ensemble", "string ensemble"), ("Harfe", "harp"), ("Banjo", "banjo"),
        ("Mandoline", "mandolin"), ("Ukulele", "ukulele"), ("Laute", "lute"), ("Sitar", "sitar"),
    ],
    "🎹 Tasten & Synths": [
        ("Klavier", "piano"), ("E-Piano (Rhodes)", "rhodes electric piano"), ("Orgel", "hammond organ"),
        ("Kirchenorgel", "church organ"), ("Synthesizer", "synthesizer"), ("Synth-Pads", "synth pads"),
        ("Synth-Lead", "synth lead"), ("Arpeggiator", "arpeggiated synth"), ("Cembalo", "harpsichord"),
        ("Akkordeon", "accordion"),
    ],
    "🎺 Bläser": [
        ("Trompete", "trumpet"), ("Posaune", "trombone"), ("Saxophon", "saxophone"), ("Querflöte", "flute"),
        ("Klarinette", "clarinet"), ("Waldhorn", "french horn"), ("Bläsersatz", "brass section"),
        ("Dudelsack", "bagpipes"), ("Tin Whistle", "tin whistle"), ("Panflöte", "pan flute"),
    ],
    "✨ Sonstiges & Klangfarben": [
        ("Orchester", "orchestra"), ("Chor", "choir"), ("Glockenspiel", "glockenspiel"), ("Marimba", "marimba"),
        ("Vibraphon", "vibraphone"), ("Kirchenglocken", "bells"), ("Didgeridoo", "didgeridoo"),
        ("Drehleier", "hurdy-gurdy"), ("Vinyl-Knistern", "vinyl crackle"), ("Naturgeräusche", "ambient nature sounds"),
    ],
}
ALLE_INSTRUMENTE = {tag: label for cat in INSTRUMENTE.values() for label, tag in cat}
LABEL_ZU_TAG = {label.lower(): tag for tag, label in ALLE_INSTRUMENTE.items()}

GESANG = {
    "Kein Gesang (instrumental)": "instrumental",
    "Männlicher Gesang": "male vocals",
    "Weiblicher Gesang": "female vocals",
    "Duett (Mann & Frau)": "male and female vocals, duet",
    "Chorgesang": "choir vocals",
    "Egal – die KI entscheidet": "vocals",
}
INSTRUMENTAL = "Kein Gesang (instrumental)"

DEFAULT_SONG = {
    "titel": "",
    "stil": "",
    "stil_en": "",
    "instrumente": [],
    "gesang": "Weiblicher Gesang",
    "idee": "",
    "songtext": "",
    "sprache": "Deutsch",
    "bpm": 110,
    "tonart": "C major",
    "takt": "4/4",
    "dauer": 150,
    "varianten": 1,
}

# Wertebereiche (auch für den Berater)
BPM_MIN, BPM_MAX = 40, 220
DAUER_MIN, DAUER_MAX = 20, 360


def mit_gesang(song):
    return song.get("gesang") != INSTRUMENTAL


def tags(song):
    """Beschreibung (englisch) für die Musik-KI."""
    teile = []
    stil = (song.get("stil_en") or "").strip() or (song.get("stil") or "").strip()
    if stil:
        teile.append(stil.rstrip(", ."))
    inst = [t for t in song.get("instrumente") or [] if t]
    if inst:
        teile.append(", ".join(inst))
    teile.append(GESANG.get(song.get("gesang"), "vocals"))
    return ", ".join(teile)


def lyrics(song):
    if not mit_gesang(song):
        return "[Instrumental]"
    txt = (song.get("songtext") or "").strip()
    if not txt:
        txt = (song.get("idee") or "").strip()
    return txt or "[Instrumental]"


def clamp(song):
    """Werte in gültige Bereiche bringen (z. B. nach Vorschlägen vom Berater)."""
    s = dict(DEFAULT_SONG)
    s.update({k: v for k, v in (song or {}).items() if v is not None})
    try:
        s["bpm"] = max(BPM_MIN, min(BPM_MAX, int(round(float(s["bpm"])))))
    except Exception:
        s["bpm"] = DEFAULT_SONG["bpm"]
    try:
        s["dauer"] = max(DAUER_MIN, min(DAUER_MAX, int(round(float(s["dauer"])))))
    except Exception:
        s["dauer"] = DEFAULT_SONG["dauer"]
    try:
        s["varianten"] = max(1, min(4, int(s["varianten"])))
    except Exception:
        s["varianten"] = 1
    if s["tonart"] not in TONARTEN:
        s["tonart"] = normalize_key(s["tonart"]) or DEFAULT_SONG["tonart"]
    if s["takt"] not in TAKTE:
        t = str(s["takt"]).strip()
        s["takt"] = next((k for k, v in TAKTE.items() if t in (v, k.split("/")[0])), "4/4")
    if s["sprache"] not in SPRACHEN:
        s["sprache"] = next((k for k, v in SPRACHEN.items() if str(s["sprache"]).lower() in (v, k.lower())), "Deutsch")
    if s["gesang"] not in GESANG:
        g = str(s["gesang"]).lower()
        s["gesang"] = next((k for k in GESANG if g and (g in k.lower() or k.lower().startswith(g))), DEFAULT_SONG["gesang"])
    inst = []
    for t in s.get("instrumente") or []:
        t = str(t).strip()
        tag = t if t in ALLE_INSTRUMENTE else LABEL_ZU_TAG.get(t.lower())
        if tag and tag not in inst:
            inst.append(tag)
    s["instrumente"] = inst
    for k in ("titel", "stil", "stil_en", "idee", "songtext"):
        s[k] = str(s.get(k) or "")
    return s


def normalize_key(k):
    """'a-moll', 'Am', 'A minor', 'Fis-Dur', 'Eb major' → 'A minor', 'F# major', 'Eb major' …"""
    if not k:
        return None
    t = str(k).strip().replace("♯", "#").replace("♭", "b")
    m = re.match(r"^([A-Ha-h])(is|es|#|b|s)?", t)
    if not m:
        return None
    letter = "B" if m.group(1).upper() == "H" else m.group(1).upper()
    acc = {"is": "#", "es": "b", "s": "b"}.get((m.group(2) or "").lower(), m.group(2) or "")
    rest = t[m.end():].strip(" -_").lower()
    low = t.lower()
    minor = "moll" in low or "minor" in low or (rest.startswith("m") and not rest.startswith("maj"))
    if "dur" in low or "major" in low or rest.startswith("maj"):
        minor = False
    cand = f"{letter}{acc} {'minor' if minor else 'major'}"
    return cand if cand in TONARTEN else None


def random_seed():
    return random.randint(1, 2**31 - 1)
