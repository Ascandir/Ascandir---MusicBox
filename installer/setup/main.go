package main

// Installationsassistent mit Fenster: Willkommen → Zielordner → Installieren → Fertig.

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/lxn/walk"
	. "github.com/lxn/walk/declarative"
)

type wizard struct {
	mw                                *walk.MainWindow
	pages                             [4]*walk.Composite
	title, sub, space, step           *walk.Label
	dir                               *walk.LineEdit
	desktop, start, runNow, openGuide *walk.CheckBox
	prog                              *walk.ProgressBar
	logTE                             *walk.TextEdit
	back, next, cancelB               *walk.PushButton
	finishText                        *walk.TextLabel
	page                              int
	installing, done, failed          bool
	autoFinish                        bool

	logMu  sync.Mutex
	logBuf []string
}

var headers = [4][2]string{
	{"Willkommen bei der Ascandir MusicBox", "Songs mit lokaler KI bauen – mit Strata als Texter und Berater."},
	{"Zielordner wählen", "Wohin soll die MusicBox installiert werden?"},
	{"Installation läuft", "Bitte warten – das dauert ein paar Minuten."},
	{"Fertig!", "Die MusicBox ist installiert."},
}

func defaultDir() string {
	if p := previousInstall(); p != "" {
		return p
	}
	return filepath.Join(os.Getenv("LOCALAPPDATA"), "Ascandir-MusicBox")
}

// --- UI-Schnittstelle für Install() ---
func (w *wizard) Step(n, total int, text string) {
	w.mw.Synchronize(func() {
		w.step.SetText(fmt.Sprintf("Schritt %d von %d: %s", n, total, text))
		w.prog.SetValue((n - 1) * 100 / total)
	})
	w.Log("── " + text)
}

func (w *wizard) Log(line string) {
	w.logMu.Lock()
	w.logBuf = append(w.logBuf, line)
	w.logMu.Unlock()
}

func (w *wizard) flushLog() {
	w.logMu.Lock()
	lines := w.logBuf
	w.logBuf = nil
	w.logMu.Unlock()
	if len(lines) == 0 {
		return
	}
	if w.logTE.TextLength() > 200000 {
		w.logTE.SetText("…\r\n")
	}
	w.logTE.AppendText(strings.Join(lines, "\r\n") + "\r\n")
}

// --- Seitenwechsel ---
func (w *wizard) show(p int) {
	w.page = p
	for i, c := range w.pages {
		c.SetVisible(i == p)
	}
	w.title.SetText(headers[p][0])
	w.sub.SetText(headers[p][1])
	w.back.SetVisible(p == 1)
	w.next.SetEnabled(p != 2)
	w.cancelB.SetVisible(p != 3)
	switch p {
	case 0:
		w.next.SetText("Weiter >")
	case 1:
		w.next.SetText("Installieren")
		w.updateSpace()
	case 2:
		w.next.SetText("Weiter >")
	case 3:
		w.next.SetText("Fertigstellen")
	}
}

func (w *wizard) updateSpace() {
	d := strings.TrimSpace(w.dir.Text())
	if g := freeGB(d); g >= 0 {
		w.space.SetText(fmt.Sprintf("Benötigt: ca. 25 GB      Frei auf %s  %.0f GB", filepath.VolumeName(d), g))
	} else {
		w.space.SetText("Benötigt: ca. 25 GB")
	}
}

func (w *wizard) browse() {
	start := filepath.Dir(strings.TrimSpace(w.dir.Text()))
	p, ok, failed := pickFolder(uintptr(w.mw.Handle()), "Ordner für die MusicBox wählen", start)
	if failed {
		// Notlösung: klassischer Dialog, ohne Startordner (sonst kommt man nicht nach oben)
		dlg := walk.FileDialog{Title: "Ordner für die MusicBox wählen"}
		if acc, err := dlg.ShowBrowseFolder(w.mw); err == nil && acc {
			p, ok = dlg.FilePath, true
		}
	}
	if !ok || p == "" {
		return
	}
	b := filepath.Base(p)
	if !strings.EqualFold(b, "Ascandir-MusicBox") {
		p = filepath.Join(p, "Ascandir-MusicBox")
	}
	w.dir.SetText(p)
	w.updateSpace()
}

func (w *wizard) onNext() {
	switch w.page {
	case 0:
		w.show(1)
	case 1:
		d := strings.TrimSpace(w.dir.Text())
		if d == "" || !filepath.IsAbs(d) {
			walk.MsgBox(w.mw, "Zielordner", "Bitte einen vollständigen Ordnerpfad angeben, z. B. D:\\Ascandir-MusicBox.", walk.MsgBoxIconWarning)
			return
		}
		if g := freeGB(d); g >= 0 && g < 25 {
			if walk.MsgBox(w.mw, "Wenig Speicherplatz",
				fmt.Sprintf("Auf diesem Laufwerk sind nur %.1f GB frei, gebraucht werden ca. 25 GB.\nTrotzdem fortfahren?", g),
				walk.MsgBoxYesNo|walk.MsgBoxIconWarning) != walk.DlgCmdYes {
				return
			}
		}
		if !writable(d) {
			walk.MsgBox(w.mw, "Kein Schreibzugriff",
				"In diesen Ordner darf die MusicBox ohne Administratorrechte nicht schreiben\n"+
					"(z. B. „C:\\Programme“). MusicBox speichert dort aber deine Songs und die Musik-KI.\n\n"+
					"Bitte einen anderen Ordner wählen, z. B. D:\\Ascandir-MusicBox oder in deinem Benutzerordner.",
				walk.MsgBoxIconWarning)
			return
		}
		for appRunning() {
			if walk.MsgBox(w.mw, "MusicBox läuft",
				"Die MusicBox ist gerade geöffnet.\nBitte im Programm oben rechts auf „Beenden“ klicken und dann OK drücken.",
				walk.MsgBoxOKCancel|walk.MsgBoxIconInformation) != walk.DlgCmdOK {
				return
			}
		}
		w.startInstall(Options{Dir: d, Desktop: w.desktop.Checked(), StartMenu: w.start.Checked()})
	case 3:
		root := strings.TrimSpace(w.dir.Text())
		if w.runNow.Checked() {
			c := exec.Command(filepath.Join(root, "MusicBox.exe"))
			c.Dir = root
			c.Start()
		}
		if w.openGuide.Checked() {
			c := exec.Command("cmd", "/c", "start", "", filepath.Join(root, "ANLEITUNG.html"))
			hidden(c)
			c.Start()
		}
		w.mw.Close()
	}
}

func (w *wizard) startInstall(o Options) {
	w.installing = true
	w.show(2)
	w.prog.SetValue(0)
	ticker := time.NewTicker(250 * time.Millisecond)
	go func() {
		for range ticker.C {
			w.mw.Synchronize(w.flushLog)
		}
	}()
	go func() {
		err := Install(o, w)
		time.Sleep(300 * time.Millisecond)
		w.mw.Synchronize(func() {
			ticker.Stop()
			w.flushLog()
			w.installing = false
			if err != nil {
				w.failed = true
				if cancel {
					w.mw.Close()
					return
				}
				w.step.SetText("❌ Fehler: " + err.Error())
				w.cancelB.SetText("Schließen")
				walk.MsgBox(w.mw, "Installation fehlgeschlagen",
					"Leider ist ein Fehler aufgetreten:\n\n"+err.Error()+
						"\n\nMach bitte einen Screenshot vom Fenster (mit dem Protokoll) und schick ihn Claude.\n"+
						"Du kannst das Setup danach einfach nochmal starten.", walk.MsgBoxIconError)
				return
			}
			w.done = true
			w.prog.SetValue(100)
			w.finishText.SetText("Die MusicBox wurde installiert in:\r\n" + o.Dir +
				"\r\n\r\nDu findest ihn auf dem Desktop und im Startmenü.\r\n" +
				"Viel Spaß beim ersten Song! Tipp: Im Tab „KI-Berater“ kannst du deinen Song mit Strata planen.")
			w.show(3)
			if w.autoFinish {
				w.openGuide.SetChecked(false)
				w.onNext() // startet das Tool neu und schließt das Setup
			}
		})
	}()
}

func (w *wizard) onCancel() {
	if w.failed {
		w.mw.Close()
		return
	}
	if walk.MsgBox(w.mw, "Abbrechen", "Installation wirklich abbrechen?", walk.MsgBoxYesNo|walk.MsgBoxIconQuestion) != walk.DlgCmdYes {
		return
	}
	if w.installing {
		cancelInstall()
		return
	}
	w.mw.Close()
}

func runWizard(update bool) {
	w := &wizard{}
	header := walk.RGB(32, 26, 21)
	amber := walk.RGB(242, 169, 59)
	err := MainWindow{
		AssignTo: &w.mw,
		Title:    "Ascandir - MusicBox Setup",
		Size:     Size{Width: 620, Height: 470},
		MinSize:  Size{Width: 560, Height: 440},
		Layout:   VBox{MarginsZero: true, SpacingZero: true},
		Children: []Widget{
			Composite{
				Background: SolidColorBrush{Color: header},
				Layout:     HBox{Margins: Margins{Left: 22, Top: 14, Right: 22, Bottom: 14}},
				Children: []Widget{
					Composite{
						Background: SolidColorBrush{Color: header},
						Layout:     VBox{MarginsZero: true, Spacing: 4, Alignment: AlignHNearVCenter},
						Children: []Widget{
							Label{AssignTo: &w.title, Font: Font{Family: "Segoe UI", PointSize: 15, Bold: true}, TextColor: amber, Background: SolidColorBrush{Color: header}},
							Label{AssignTo: &w.sub, Font: Font{Family: "Segoe UI", PointSize: 10}, TextColor: walk.RGB(225, 214, 200), Background: SolidColorBrush{Color: header}},
						},
					},
					HSpacer{},
				},
			},
			Composite{
				Layout: VBox{Margins: Margins{Left: 24, Top: 18, Right: 24, Bottom: 8}, Alignment: AlignHNearVNear},
				Children: []Widget{
					// Seite 0 – Willkommen
					Composite{AssignTo: &w.pages[0], Layout: VBox{MarginsZero: true, Alignment: AlignHNearVNear}, Children: []Widget{
						TextLabel{Font: Font{Family: "Segoe UI", PointSize: 10}, Text: "Mit der Ascandir MusicBox baust du Songs mit KI: Instrumente anklicken, Stil und Feeling beschreiben, " +
							"BPM wählen – und deinen groben Text lässt du von Strata zu einem richtigen Songtext machen.\r\n\r\n" +
							"Das Setup richtet alles Nötige ein:\r\n" +
							"   •  Python (eigene Kopie, ändert nichts an deinem System)\r\n" +
							"   •  Microsoft Visual C++ Laufzeit, falls sie fehlt\r\n" +
							"   •  die Musik-KI ComfyUI mit ACE-Step 1.5 (für AMD ROCm oder NVIDIA, passend zu deiner Grafikkarte)\r\n" +
							"   •  die KI-Modelle (Musik-Modell, Song-Planer, Klang-Decoder)\r\n\r\n" +
							"Einmalig werden ca. 12 GB heruntergeladen. Danach läuft alles offline auf deinem PC. Dein Grafiktreiber wird nicht verändert.\r\n\r\n" +
							"Klicke auf „Weiter“, um fortzufahren."},
						VSpacer{},
					}},
					// Seite 1 – Zielordner
					Composite{AssignTo: &w.pages[1], Visible: false, Layout: VBox{MarginsZero: true, Spacing: 8, Alignment: AlignHNearVNear}, Children: []Widget{
						TextLabel{Font: Font{Family: "Segoe UI", PointSize: 10}, Text: "Die MusicBox wird in den folgenden Ordner installiert. Dort landen auch deine Songs und die Musik-KI (ca. 25 GB).\r\n" +
							"Um einen anderen Ordner zu wählen, klicke auf „Durchsuchen …“."},
						Composite{Layout: HBox{MarginsZero: true}, Children: []Widget{
							LineEdit{AssignTo: &w.dir, Text: defaultDir(), Font: Font{Family: "Segoe UI", PointSize: 10},
								OnEditingFinished: func() { w.updateSpace() }},
							PushButton{Text: "Durchsuchen …", OnClicked: func() { w.browse() }},
						}},
						Label{AssignTo: &w.space, Font: Font{Family: "Segoe UI", PointSize: 9}, TextColor: walk.RGB(90, 90, 90)},
						VSpacer{Size: 10},
						CheckBox{AssignTo: &w.desktop, Text: "Verknüpfung auf dem Desktop anlegen", Checked: true},
						CheckBox{AssignTo: &w.start, Text: "Eintrag im Startmenü anlegen", Checked: true},
						VSpacer{},
					}},
					// Seite 2 – Installieren
					Composite{AssignTo: &w.pages[2], Visible: false, Layout: VBox{MarginsZero: true, Spacing: 8, Alignment: AlignHNearVNear}, Children: []Widget{
						Label{AssignTo: &w.step, Font: Font{Family: "Segoe UI", PointSize: 10, Bold: true}, Text: "Vorbereiten …"},
						ProgressBar{AssignTo: &w.prog, MaxValue: 100, MinSize: Size{Height: 22}},
						Label{Text: "Protokoll:", Font: Font{Family: "Segoe UI", PointSize: 9}},
						TextEdit{AssignTo: &w.logTE, ReadOnly: true, VScroll: true, Font: Font{Family: "Consolas", PointSize: 9}},
					}},
					// Seite 3 – Fertig
					Composite{AssignTo: &w.pages[3], Visible: false, Layout: VBox{MarginsZero: true, Spacing: 8, Alignment: AlignHNearVNear}, Children: []Widget{
						TextLabel{AssignTo: &w.finishText, Font: Font{Family: "Segoe UI", PointSize: 10}},
						VSpacer{Size: 12},
						CheckBox{AssignTo: &w.runNow, Text: "MusicBox jetzt starten", Checked: true},
						CheckBox{AssignTo: &w.openGuide, Text: "Anleitung öffnen", Checked: true},
						VSpacer{},
					}},
				},
			},
			Composite{
				Layout: HBox{Margins: Margins{Left: 16, Top: 8, Right: 16, Bottom: 14}},
				Children: []Widget{
					HSpacer{},
					PushButton{AssignTo: &w.back, Text: "< Zurück", MinSize: Size{Width: 95}, OnClicked: func() { w.show(0) }},
					PushButton{AssignTo: &w.next, Text: "Weiter >", MinSize: Size{Width: 110}, OnClicked: func() { w.onNext() }},
					PushButton{AssignTo: &w.cancelB, Text: "Abbrechen", MinSize: Size{Width: 95}, OnClicked: func() { w.onCancel() }},
				},
			},
		},
	}.Create()
	if err != nil {
		walk.MsgBox(nil, "Ascandir - MusicBox Setup", "Fenster konnte nicht erstellt werden: "+err.Error(), walk.MsgBoxIconError)
		return
	}
	if ic, err := walk.NewIconFromResourceId(1); err == nil {
		w.mw.SetIcon(ic)
	} else if ic, err := walk.NewIconFromResource("APP"); err == nil {
		w.mw.SetIcon(ic)
	}
	w.mw.Closing().Attach(func(canceled *bool, reason walk.CloseReason) {
		if w.installing && !cancel {
			*canceled = true
			w.onCancel()
		}
	})
	w.show(0)
	if update && previousInstall() != "" {
		// Update: ohne Fragen in den bisherigen Ordner installieren
		dir := previousInstall()
		w.dir.SetText(dir)
		for i := 0; i < 40 && appRunning(); i++ { // Tool beendet sich gerade selbst
			time.Sleep(250 * time.Millisecond)
		}
		headers[2] = [2]string{"Update wird installiert", "Deine Songs und Einstellungen bleiben erhalten."}
		w.startInstall(Options{Dir: dir, Desktop: shortcutExists("Desktop"), StartMenu: shortcutExists("Programs")})
		w.autoFinish = true
	}
	w.mw.Run()
}

func runUninstall() {
	exe, _ := os.Executable()
	root := filepath.Dir(exe)
	if walk.MsgBox(nil, "MusicBox entfernen", "Die MusicBox wirklich deinstallieren?",
		walk.MsgBoxYesNo|walk.MsgBoxIconQuestion) != walk.DlgCmdYes {
		return
	}
	for appRunning() {
		if walk.MsgBox(nil, "MusicBox läuft", "Bitte die MusicBox zuerst beenden (im Programm oben rechts „Beenden“).",
			walk.MsgBoxOKCancel|walk.MsgBoxIconInformation) != walk.DlgCmdOK {
			return
		}
	}
	del := walk.MsgBox(nil, "Deine Daten", "Sollen auch deine Songs und Einstellungen gelöscht werden?\n\n"+
		"„Nein“ behält sie im Ordner:\n"+root, walk.MsgBoxYesNo|walk.MsgBoxIconQuestion|walk.MsgBoxDefButton2) == walk.DlgCmdYes
	Uninstall(root, del)
	walk.MsgBox(nil, "MusicBox", "Die MusicBox wurde entfernt.", walk.MsgBoxIconInformation)
}

func main() {
	if len(os.Args) > 1 && strings.EqualFold(os.Args[1], "/uninstall") {
		runUninstall()
		return
	}
	update := len(os.Args) > 1 && strings.EqualFold(os.Args[1], "/update")
	runWizard(update)
}
