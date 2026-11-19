"""Strata als Songwriter (Knopf „Songtext schreiben“) und als KI-Berater, der die Regler setzt."""
import json
import re
import threading
import time

from . import comfy as M
from . import config as C
from . import llm as L
from . import song as S
from .debuglog import log


def _free_gpu_for_strata():
    """Grafikspeicher für Strata frei machen (die Musik-KI braucht ihn sonst)."""
    if M.BUSY.locked():
        raise RuntimeError("Gerade wird ein Song erzeugt – warte, bis er fertig ist.")
    if M.stop():
        log.info("Musik-KI für Strata beendet")


# ---------- Songtext ----------
SONGWRITER = ("Du bist ein erfahrener Songwriter und Texter. Du schreibst singbare, bildhafte Songtexte mit klarem "
              "Aufbau, sauberem Rhythmus und natürlichen Reimen. Du hältst dich an die Wünsche und Ideen des "
              "Nutzers und lieferst nur das verlangte Format – ohne Erklärungen.")


def _laenge(dauer):
    if dauer <= 60:
        return "kurz: [Verse 1] (4 Zeilen) und [Chorus] (4 Zeilen)"
    if dauer <= 150:
        return "mittel: [Intro], [Verse 1], [Chorus], [Verse 2], [Chorus], [Outro] – Strophen 4–8 Zeilen, Refrain 4 Zeilen"
    if dauer <= 240:
        return ("normal: [Intro], [Verse 1], [Pre-Chorus], [Chorus], [Verse 2], [Pre-Chorus], [Chorus], [Bridge], "
                "[Chorus], [Outro] – Strophen 6–8 Zeilen, Refrain 4–6 Zeilen")
    return ("lang: [Intro], [Verse 1], [Pre-Chorus], [Chorus], [Verse 2], [Pre-Chorus], [Chorus], [Instrumental Break], "
            "[Verse 3], [Bridge], [Chorus], [Chorus], [Outro]")


def lyrics_prompt(song):
    inst = ", ".join(S.ALLE_INSTRUMENTE.get(t, t) for t in song.get("instrumente") or []) or "frei"
    return f"""Schreib aus meiner Idee einen fertigen Songtext.

Meine Idee / grober Text:
\"\"\"{song.get('idee') or '(keine – denk dir passend zum Stil etwas aus)'}\"\"\"

Musikrichtung & Feeling: {song.get('stil') or 'frei'}
Gesang: {song.get('gesang')}
Instrumente: {inst}
Tempo: {song.get('bpm')} BPM, Takt {song.get('takt')}, Tonart {song.get('tonart')}
Länge des Songs: ca. {song.get('dauer')} Sekunden → Aufbau {_laenge(int(song.get('dauer') or 150))}
Sprache des Songtexts: {song.get('sprache')}

Regeln:
- Übernimm meine Kernaussagen, Namen und besonders gelungene Formulierungen.
- Abschnitts-Marken immer auf Englisch in eckigen Klammern auf eigener Zeile, z. B. [Verse 1], [Chorus], [Bridge].
- Kurze, singbare Zeilen. Keine Regieanweisungen, keine Akkorde, keine Erklärungen.
- Zusätzlich: eine englische Stilbeschreibung für eine Musik-KI (kommagetrennte Stichworte: Genre, Stimmung,
  Tempo-Gefühl, Klangfarbe, Produktion – höchstens 30 Wörter) und ein kurzer Songtitel.
  WICHTIG: In der Stilbeschreibung KEIN Geschlecht und keine Stimme nennen (kein male/female/choir) – das regelt die App.

Antworte GENAU in diesem Format:
TITEL: <Titel>
STIL: <englische Stichworte>
SONGTEXT:
<der Songtext>"""


def parse_lyrics(text):
    t = re.sub(r"```[a-zA-Z]*", "", text or "").replace("```", "").strip()
    titel = stil = ""
    m = re.search(r"(?im)^\s*\**\s*titel\s*\**\s*:\s*(.+)$", t)
    if m:
        titel = m.group(1).strip().strip('*"„“ ')
    m = re.search(r"(?im)^\s*\**\s*stil\s*\**\s*:\s*(.+)$", t)
    if m:
        stil = m.group(1).strip().strip('*" ')
    m = re.search(r"(?is)^\s*\**\s*songtext\s*\**\s*:\s*\n?(.*)$", t, re.M)
    if m:
        body = m.group(1)
    else:
        body = "\n".join(l for l in t.splitlines()
                         if not re.match(r"(?i)^\s*\**\s*(titel|stil)\s*\**\s*:", l))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return titel, stil, body


def write_lyrics(song, progress=lambda f, m: None):
    """Startet Strata (falls nötig), schreibt den Songtext, beendet Strata wieder. → (titel, stil_en, songtext)"""
    _free_gpu_for_strata()
    with L.KI(progress) as url:
        progress(0.97, "✍️ Strata schreibt den Songtext …")
        BERATER.touch()
        cl, model = L.client(url)
        ans = L.chat(cl, model, [{"role": "system", "content": SONGWRITER},
                                 {"role": "user", "content": lyrics_prompt(song)}], max_tokens=3000, temperature=0.8)
    log.info("Songtext geschrieben (%d Zeichen)", len(ans))
    titel, stil, body = parse_lyrics(ans)
    if not body:
        raise RuntimeError("Strata hat keinen Songtext geliefert. Versuch es nochmal.")
    return titel, stil, body


# ---------- Berater ----------
def berater_system(song):
    inst = ", ".join(sorted(S.ALLE_INSTRUMENTE.values()))
    cur = {k: song.get(k) for k in ("titel", "stil", "stil_en", "gesang", "stimmklang", "sprache", "bpm", "tonart", "takt",
                                     "dauer", "idee", "songtext")}
    cur["instrumente"] = [S.ALLE_INSTRUMENTE.get(t, t) for t in song.get("instrumente") or []]
    return f"""Du bist der KI-Berater in der „Ascandir MusicBox“, einer App, die mit der Musik-KI ACE-Step Songs erzeugt.
Du besprichst mit dem Nutzer seinen Song wie ein erfahrener Musikproduzent: Genre, Stimmung, Tempo, Instrumente,
Aufbau und Text. Du kannst die Regler der App direkt einstellen.

AKTUELLE EINSTELLUNGEN:
{json.dumps(cur, ensure_ascii=False, indent=1)}

REGLER, DIE DU SETZEN KANNST:
- titel: Songtitel
- stil: Musikrichtung & Feeling auf Deutsch, so wie der Nutzer es lesen soll
- stil_en: dieselbe Beschreibung auf Englisch als kommagetrennte Stichworte für die Musik-KI
  (Genre, Subgenre, Stimmung, Tempo-Gefühl, Produktion/Klang). Setz stil_en IMMER mit, wenn du stil änderst.
  Die Stimme (Mann/Frau/Chor) NICHT in stil/stil_en schreiben – dafür gibt es den Regler "gesang".
- instrumente: Liste, NUR aus: {inst}
- gesang: eine von: {", ".join(S.GESANG)}
- stimmklang (Autotune ja/nein): eine von: {", ".join(S.STIMMKLANG)}
- sprache (des Songtexts): eine von: {", ".join(M.SPRACHEN)}
- bpm: Zahl {S.BPM_MIN}–{S.BPM_MAX}
- tonart: z. B. "A minor", "F# major", "Eb major"
- takt: eine von: {", ".join(M.TAKTE)}
- dauer: Länge in Sekunden ({S.DAUER_MIN}–{S.DAUER_MAX})
- idee: grober Inhalt / Stichpunkte für den Text
- songtext: fertiger Songtext mit Abschnitts-Marken wie [Verse 1], [Chorus], [Bridge] (Marken auf Englisch).
  Nur schreiben, wenn der Nutzer einen Text möchte oder zustimmt.

REGELN:
- Antworte auf Deutsch, locker, kurz und konkret. Frag nach, wenn etwas Wichtiges unklar ist.
- Wenn du Regler ändern willst, hänge am ENDE deiner Antwort genau einen Block an – nur mit den Feldern, die sich ändern:
```regler
{{"bpm": 92, "tonart": "A minor", "instrumente": ["Klavier", "Cello"]}}
```
- "instrumente" ist immer die komplette neue Liste.
- Sag in einem Satz, was du eingestellt hast. Keine anderen Felder erfinden."""


def parse_regler(text):
    """→ (Text ohne Block, Änderungen als dict)"""
    blocks = list(re.finditer(r"```(?:regler|json)?\s*(\{.*?\})\s*```", text or "", re.S))
    if not blocks:
        m = re.search(r"(?s)(\{\s*\"(?:titel|stil|bpm|instrumente|gesang|tonart|dauer|songtext|idee|sprache|takt)\".*\})\s*$",
                      text or "")
        if not m:
            return (text or "").strip(), {}
        raw, span = m.group(1), m.span()
    else:
        b = blocks[-1]
        raw, span = b.group(1), b.span()
    data = None
    for cand in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
        try:
            data = json.loads(cand)
            break
        except Exception:
            pass
    if not isinstance(data, dict):
        log.info("Regler-Block nicht lesbar: %s", raw[:300])
        return (text or "").strip(), {}
    clean = ((text or "")[:span[0]] + (text or "")[span[1]:]).strip()
    allowed = set(S.DEFAULT_SONG) - {"varianten", "qualitaet", "schritte"}
    return clean, {k: v for k, v in data.items() if k in allowed}


def describe_changes(old, new):
    names = {"titel": "Titel", "stil": "Stil", "stil_en": "Stil (für die KI)", "instrumente": "Instrumente",
             "gesang": "Gesang", "stimmklang": "Stimmklang", "sprache": "Sprache", "bpm": "BPM", "tonart": "Tonart", "takt": "Takt",
             "dauer": "Länge", "idee": "Idee", "songtext": "Songtext"}
    out = []
    for k, label in names.items():
        if old.get(k) == new.get(k):
            continue
        v = new.get(k)
        if k == "instrumente":
            v = ", ".join(S.ALLE_INSTRUMENTE.get(t, t) for t in v) or "keine"
        elif k == "dauer":
            v = f"{v // 60}:{v % 60:02d} min"
        elif k in ("songtext", "idee", "stil_en"):
            v = "aktualisiert"
        out.append(f"{label}: {v}")
    return out


class Berater:
    def __init__(self):
        self.ki = None
        self.url = None
        self.lock = threading.Lock()
        self.last = 0.0

    def touch(self):
        self.last = time.time()

    def active(self):
        return self.ki is not None and self.ki.started

    def ensure(self, progress):
        if self.url and L.api_up(self.url):
            return self.url
        self.stop()
        _free_gpu_for_strata()
        ki = L.KI(progress)
        self.url = ki.__enter__()
        self.ki = ki
        return self.url

    def ask(self, history, song, progress=lambda f, m: None):
        """history: [{'role','content'}] inkl. neuer Nutzer-Nachricht. → (Antworttext, neue Song-Werte, Änderungsliste)"""
        with self.lock:
            self.touch()
            try:
                url = self.ensure(progress)
                progress(0.98, "💬 Strata denkt nach …")
                cl, model = L.client(url)
                msgs = [{"role": "system", "content": berater_system(song)}] + \
                       [{"role": m["role"], "content": L.text_of(m.get("content"))} for m in history[-24:]
                        if m.get("role") in ("user", "assistant") and L.text_of(m.get("content")).strip()
                        and not L.text_of(m.get("content")).startswith("❌")]
                ans = L.chat(cl, model, msgs, max_tokens=3000, temperature=0.7)
            except Exception:
                self.stop()
                raise
            finally:
                self.touch()
        text, changes = parse_regler(ans)
        new = S.clamp({**song, **changes}) if changes else song
        return text or "(keine Antwort)", new, describe_changes(song, new)

    def stop(self):
        ki, self.ki, self.url = self.ki, None, None
        if ki is not None:
            try:
                ki.__exit__(None, None, None)
            except Exception:
                log.exception("Berater-KI ließ sich nicht sauber beenden")
            return True
        return False

    def idle_check(self):
        mins = float(C.settings().get("berater_leerlauf_min", 10) or 0)
        if self.ki is not None and mins > 0 and not self.lock.locked() and time.time() - self.last > mins * 60:
            log.info("Berater %s Min. ruhig – Strata wird beendet", mins)
            self.stop()


BERATER = Berater()
