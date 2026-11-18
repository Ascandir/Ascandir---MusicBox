#!/usr/bin/env bash
# Baut Ascandir-MusicBox-Setup.exe (Cross-Compile mit Go, läuft auf Linux/macOS/Windows-Bash).
# Benötigt: Go >= 1.24, go-winres (go install github.com/tc-hib/go-winres@latest)
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(cat app/VERSION)
WINRES=${WINRES:-go-winres}
export GOFLAGS=-mod=mod

# 1) Starter (ohne Konsolenfenster)
( cd installer/launcher
  $WINRES simply --icon ../../assets/icon.png --manifest gui --product-name "Ascandir - MusicBox" \
     --file-description "Ascandir - MusicBox" --product-version "$VERSION" --file-version "$VERSION" >/dev/null
  GOOS=windows GOARCH=amd64 go build -ldflags "-H windowsgui -s -w" -o MusicBox.exe . )

# 2) Nutzlast für das Setup
P=installer/setup/payload
rm -rf "$P"; mkdir -p "$P"
cp -r app "$P/app"; find "$P" -name __pycache__ -prune -exec rm -rf {} +
cp ANLEITUNG.html assets/icon.ico "$P/"
cp installer/launcher/MusicBox.exe "$P/"

# 3) Setup-Assistent
( cd installer/setup
  sed -i.bak "s/^const appVersion = .*/const appVersion = \"$VERSION\"/" install.go && rm -f install.go.bak
  $WINRES simply --icon ../../assets/icon.png --manifest gui --product-name "Ascandir - MusicBox Setup" \
     --file-description "Ascandir - MusicBox Setup" --product-version "$VERSION" --file-version "$VERSION" >/dev/null
  GOOS=windows GOARCH=amd64 go build -ldflags "-H windowsgui -s -w" -o ../../Ascandir-MusicBox-Setup.exe . )
echo "Fertig: Ascandir-MusicBox-Setup.exe (Version $VERSION)"
