"""Supprimer l'installateur téléchargé (setup.exe + archives .bin) une fois le jeu installé.

Garde-fou : on ne supprime un dossier entier que s'il ressemble à un repack (setup.exe et
archives .bin à côté) et que ce n'est ni le dossier personnel, ni Téléchargements, ni le
dossier des jeux. Sinon, seuls l'installateur et ses archives sont supprimés.
"""

import os
import re
import shutil
from pathlib import Path

from . import config

ARCHIVE = re.compile(r"^(fg-.*|setup-\d+|.*-\d+)\.bin$", re.IGNORECASE)
SETUP_NAMES = {"setup", "install", "installer"}


def _protected() -> set[Path]:
    home = Path.home()
    names = ["", "Téléchargements", "Downloads", "Bureau", "Desktop", "Documents", "Games"]
    out = {home / n for n in names} | {Path("/"), Path("/mnt"), Path("/var/mnt")}
    games = config.games_dir()
    out |= {games, *games.parents}
    return {p.resolve() for p in out if p.exists()} | out


def _archives(folder: Path) -> list[Path]:
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and ARCHIVE.match(p.name))
    except OSError:
        return []


def targets(installer: str | Path) -> list[Path]:
    """Ce qu'il faut supprimer pour retirer cet installateur ([] s'il n'existe plus)."""
    inst = Path(installer)
    if not inst.is_file():
        return []
    folder = inst.parent
    archives = _archives(folder)
    whole_folder = (
        inst.stem.lower() in SETUP_NAMES
        and archives
        and folder.resolve() not in _protected()
        and folder not in _protected()
    )
    if whole_folder:
        return [folder]
    return [inst, *archives]


def size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        if p.is_dir():
            for root, _dirs, files in os.walk(p):
                for f in files:
                    try:
                        total += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
        elif p.exists():
            total += p.stat().st_size
    return total


def delete(paths: list[Path]) -> None:
    for p in paths:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
