"""Entrées du menu K et icônes extraites des .exe."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import APP_NAME
from .library import Game

APPS_DIR = Path.home() / ".local/share/applications"
PREFIX = "fatman-games-"


def launcher_command() -> str:
    """La commande installée par install.sh, sinon le script du dépôt."""
    installed = Path.home() / ".local/bin/fatman-games"
    if installed.exists():
        return str(installed)
    return str(Path(__file__).resolve().parent.parent / "fatman-games")


def entry_path(game: Game) -> Path:
    return APPS_DIR / f"{PREFIX}{game.slug}.desktop"


def _escape(s: str) -> str:
    return s.replace("\n", " ").strip()


def write_entry(game: Game) -> Path:
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    icon = str(game.icon) if game.icon.exists() else "applications-games"
    p = entry_path(game)
    p.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_escape(game.name)}\n"
        "Comment=Jeu installé avec FatMan Games\n"
        f'Exec="{launcher_command()}" --play {game.slug}\n'
        f"Icon={icon}\n"
        "Categories=Game;\n"
        "Terminal=false\n"
        "X-FatmanGames=true\n"
    )
    refresh_menu()
    return p


def remove_entry(game: Game) -> None:
    entry_path(game).unlink(missing_ok=True)
    refresh_menu()


def refresh_menu() -> None:
    if shutil.which("kbuildsycoca6"):
        subprocess.Popen(["kbuildsycoca6"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


def extract_icon(exe: Path, dest: Path) -> bool:
    """Tire la plus grande icône du .exe (wrestool + icotool) vers `dest` en PNG."""
    if not (shutil.which("wrestool") and shutil.which("icotool")):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        subprocess.run(["wrestool", "-x", "-t", "14", "-o", str(t), str(exe)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for ico in t.glob("*.ico"):
            subprocess.run(["icotool", "-x", "-o", str(t), str(ico)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pngs = list(t.glob("*.png"))
        if not pngs:
            return False
        best = max(pngs, key=lambda p: p.stat().st_size)
        shutil.copyfile(best, dest)
        return True


def notify(title: str, body: str = "", icon: str = "") -> None:
    """Notification KDE (passe par notify-send, présent partout)."""
    if not shutil.which("notify-send"):
        return
    subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", icon or "applications-games", title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
