"""Réglages : dossier des jeux et Proton à utiliser."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fatman-games"
CONFIG_FILE = CONFIG_DIR / "config.json"

COMPAT_DIRS = [
    Path.home() / ".local/share/Steam/compatibilitytools.d",
    Path.home() / ".steam/steam/compatibilitytools.d",
    Path("/usr/share/steam/compatibilitytools.d"),  # paquets proton-cachyos d'Arch/CachyOS
]
PREFERRED_PROTONS = ["Proton-CachyOS Latest", "proton-cachyos", "proton-cachyos-slr"]

DEFAULTS = {
    # /mnt/GAMES sur le poste Bazzite ; ailleurs ~/Games/FatMan.
    "games_dir": "/mnt/GAMES/FatMan" if Path("/mnt/GAMES").is_dir() else str(Path.home() / "Games/FatMan"),
    # Chemin d'un Proton (dossier contenant le script `proton`), ou vide = détection auto.
    "proton": "",
    # Derniers choix de la fenêtre « Installer un jeu ».
    "auto_install": True,
    "delete_installer": False,
    # Couper la musique des installateurs (repacks).
    "mute_installer": True,
    # Empêcher l'installateur d'ouvrir des pages web (site du repack) et d'écrire dans le hosts du préfixe.
    "block_installer_web": True,
    # Ne pas lancer les extras de fin d'installation : vérification QuickSFV (fenêtre qui
    # bloque la fin) et installateur web DirectX (inutile avec Proton).
    "skip_installer_extras": True,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def games_dir(cfg: dict | None = None) -> Path:
    return Path((cfg or load())["games_dir"]).expanduser()


def list_protons() -> list[Path]:
    """Tous les Proton installés (dossiers contenant le script `proton`), sans doublon."""
    seen, out = set(), []
    for d in COMPAT_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if (p / "proton").exists() and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    return out


def find_proton(cfg: dict | None = None) -> str:
    """Renvoie le PROTONPATH à donner à umu-run.

    Ordre : réglage explicite, Proton-CachyOS Latest, le GE-Proton le plus récent,
    n'importe quel Proton local, sinon « GE-Proton » (umu le télécharge lui-même).
    """
    cfg = cfg or load()
    if cfg.get("proton") and (Path(cfg["proton"]).expanduser() / "proton").exists():
        return str(Path(cfg["proton"]).expanduser())
    found = []
    for d in COMPAT_DIRS:
        if d.is_dir():
            found += [p for p in d.iterdir() if (p / "proton").exists()]
    for name in PREFERRED_PROTONS:
        for p in found:
            if p.name == name:
                return str(p)
    ge = sorted((p for p in found if p.name.startswith("GE-Proton")), key=lambda p: p.name)
    if ge:
        return str(ge[-1])
    if found:
        return str(sorted(found)[-1])
    return "GE-Proton"
