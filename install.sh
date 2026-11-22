#!/bin/bash
# Installe FatMan Games pour l'utilisateur courant (aucun droit root nécessaire).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_ID="io.github.judikaelle.FatmanGames"
mkdir -p ~/.local/bin ~/.local/share/applications
ln -sf "$HERE/fatman-games" ~/.local/bin/fatman-games
cat > ~/.local/share/applications/$APP_ID.desktop <<DESK
[Desktop Entry]
Type=Application
Name=FatMan Games
GenericName=Installateur de jeux Windows
Comment=Installe un setup.exe sous Proton et liste les jeux installés
Exec=$HOME/.local/bin/fatman-games %f
Icon=$HERE/data/fatman-games.svg
Categories=Game;
MimeType=application/x-ms-dos-executable;application/x-msdownload;application/vnd.microsoft.portable-executable;application/x-msdos-program;
Terminal=false
StartupWMClass=$APP_ID
DESK
update-desktop-database ~/.local/share/applications 2>/dev/null || true
kbuildsycoca6 >/dev/null 2>&1 || true
echo "Installé : menu K → « FatMan Games », ou clic droit sur un .exe → Ouvrir avec → FatMan Games."
