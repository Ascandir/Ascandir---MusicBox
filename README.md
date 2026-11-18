# 🎵 Ascandir · MusicBox

Songs mit lokaler KI bauen: Instrumente anklicken, Musikrichtung & Feeling beschreiben, BPM einstellen –
und deinen groben Text macht **Strata** auf Knopfdruck zu einem richtigen Songtext. Im **KI-Berater** besprichst du
den Song mit Strata, und er stellt die Regler direkt ein. **Alles läuft lokal auf deinem PC.**

## Installation
Neueste `Ascandir-MusicBox-Setup.exe` unter [Releases](../../releases/latest) herunterladen und starten.
Der Assistent fragt nach dem Zielordner und richtet alles selbst ein (Python, Visual C++ Laufzeit,
ComfyUI für AMD ROCm oder NVIDIA, die ACE-Step-1.5-Modelle). Updates gibt es im Tool über **⬆️ Updates**.

## Funktionen
- **🎼 Song bauen**: Titel, Musikrichtung & Feeling, ~60 Instrumente in 5 Gruppen, Gesang (oder instrumental),
  Sprache, BPM, Tonart, Takt, Länge, Varianten, Seed
- **✨ Songtext mit Strata**: Idee rein → fertiger Text mit `[Verse]`/`[Chorus]` + englische Stilbeschreibung für die
  Musik-KI. Strata wird dafür gestartet und danach wieder beendet.
- **💬 KI-Berater**: Chat mit Strata, der die aktuellen Regler kennt und sie per Antwort setzt
  (bleibt an, solange ihr redet; aus nach Ruhezeit oder beim Song-Erzeugen)
- **🎧 Meine Songs**: alle Songs (FLAC) mit gespeicherten Einstellungen, abspielen, übernehmen, löschen
- Grafikspeicher-Wechsel: Strata und Musik-KI laufen nie gleichzeitig (16 GB VRAM)

## Technik
| Teil | Inhalt |
|---|---|
| Musik-KI | [ACE-Step 1.5](https://github.com/ACE-Step/ACE-Step-1.5) (MIT) über ComfyUI portable, Modelle von `Comfy-Org/ace_step_1.5_ComfyUI_files` |
| Sprach-KI | Strata (OpenAI-kompatible API), Start/Stopp über `strata launch --preset … / --stop` |
| `app/` | Python-App (Gradio im eigenen Fenster, pywebview). Version in `app/VERSION` |
| `installer/setup/` | Setup-Assistent (Go, lxn/walk) |
| `installer/launcher/` | `MusicBox.exe` – startet die App ohne Konsolenfenster |

## Bauen
```bash
go install github.com/tc-hib/go-winres@latest
./build.sh   # erzeugt Ascandir-MusicBox-Setup.exe
```
Neue Version: `app/VERSION` erhöhen und `CHANGELOG-aktuell.md` anpassen, auf main pushen – GitHub baut das Release mit der Setup-Datei automatisch.
