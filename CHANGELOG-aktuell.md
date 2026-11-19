## 🎵 MusicBox 0.2.2 – nichts mehr auf der Systempartition

- **Alle Zwischenspeicher liegen jetzt im Tool-Ordner** (`daten\cache`, `daten\tmp`) statt auf C::
  - Kopien abgespielter Songs (Gradio), Windows-Temp von MusicBox und Musik-KI
  - AMD-Kernel-Cache (MIOpen/Comgr), NVIDIA-Cache, PyTorch-/Hugging-Face-Cache
  - Alte Temp-Dateien werden beim Start automatisch gelöscht
- **Neu unter ⚙️ Einstellungen → 💾 Speicherplatz:** Systempartition auf Altlasten früherer Versionen prüfen und mit einem Klick löschen
- Beim Deinstallieren werden auch die Zwischenspeicher entfernt
