#!/bin/bash
# Retire l'appli du menu. Les jeux installés (/mnt/GAMES/FatMan) ne sont PAS supprimés.
set -euo pipefail
rm -f ~/.local/bin/fatman-games ~/.local/share/applications/io.github.judikaelle.FatmanGames.desktop
rm -f ~/.local/share/applications/fatman-games-*.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
kbuildsycoca6 >/dev/null 2>&1 || true
echo "FatMan Games retiré du menu. Les jeux restent dans le dossier configuré."
