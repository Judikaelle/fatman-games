"""Sauvegarde et restauration des « saves » : tout le profil Windows du jeu (steamuser),
sans les caches. Archives dans <dossier des jeux>/_sauvegardes/<id>-<date>.tar.gz.
"""

import re
import tarfile
import time
from pathlib import Path

from . import config
from .library import Game

# Dossiers volumineux et jetables à ne pas archiver (comparaison en minuscules).
SKIP = {
    "temp", "d3dscache", "nvidia", "nvidia corporation", "shadercache", "shader_cache",
    "crashes", "crashdumps", "inetcache", "webcache", "cache", "gpucache", "code cache",
}


def backup_dir() -> Path:
    return config.games_dir() / "_sauvegardes"


def profile(game: Game) -> Path:
    return game.prefix / "drive_c/users/steamuser"


def _filter(info: tarfile.TarInfo):
    parts = [p.lower() for p in Path(info.name).parts]
    if any(p in SKIP for p in parts) or info.name.lower().endswith(".dmp"):
        return None
    if info.issym() or info.islnk():  # liens vers l'extérieur du préfixe : inutiles
        return None
    return info


def backup(game: Game) -> Path | None:
    src = profile(game)
    if not src.is_dir():
        return None
    out = backup_dir()
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{game.slug}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    tmp = dest.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(src, arcname="steamuser", filter=_filter)
    tmp.replace(dest)
    return dest


def backups(slug: str) -> list[Path]:
    d = backup_dir()
    if not d.is_dir():
        return []
    pat = re.compile(rf"^{re.escape(slug)}-\d{{8}}-\d{{6}}\.tar\.gz$")
    return sorted((p for p in d.iterdir() if pat.match(p.name)), reverse=True)


def restore(game: Game, archive: Path) -> None:
    dest = game.prefix / "drive_c/users"
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest, filter="data")
