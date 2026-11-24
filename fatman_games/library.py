"""La bibliothèque : un dossier par jeu dans games_dir, décrit par game.json.

Pas de base centrale : la liste, c'est le contenu du disque. Supprimer un dossier
à la main retire le jeu de la liste, le recopier ailleurs ne casse rien.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .detect import slugify

META = "game.json"

INSTALLING = "installing"
NEEDS_EXE = "needs_exe"
READY = "ready"
FAILED = "failed"         # installation automatique sans résultat


@dataclass
class Game:
    slug: str
    name: str
    status: str = INSTALLING
    exe: str = ""            # relatif au dossier du jeu
    # Réglages par jeu
    launch_args: str = ""    # arguments passés au .exe
    env: str = ""            # « CLE=valeur CLE2=valeur »
    proton: str = ""         # dossier d'un Proton précis, vide = celui par défaut
    mangohud: bool = False
    gamescope: bool = False
    gamescope_args: str = "-f"
    xinput_pad: bool = True     # manette PlayStation présentée au jeu comme une manette Xbox (XInput)
    installer: str = ""
    install_mode: str = ""      # « auto » (sans fenêtre) ou « window »
    delete_installer: bool = False  # supprimer le setup.exe téléchargé une fois le jeu prêt
    installed_at: float = field(default_factory=time.time)
    last_played: float = 0.0
    playtime: float = 0.0       # secondes
    steam_appid: int = 0        # id boutique Steam : sert uniquement à récupérer la jaquette
    steam_shortcut: int = 0     # id du raccourci « jeu non-Steam », 0 = pas dans Steam
    cover_checked: bool = False
    hero_checked: bool = False  # grande illustration Steam (bandeau « Reprendre ») cherchée
    dir: Path = field(default=None, repr=False, compare=False)

    @property
    def prefix(self) -> Path:
        return self.dir / "pfx"

    @property
    def exe_path(self) -> Path | None:
        return self.dir / self.exe if self.exe else None

    @property
    def icon(self) -> Path:
        return self.dir / "icon.png"

    @property
    def cover(self) -> Path:
        return self.dir / "cover.jpg"

    @property
    def hero(self) -> Path:
        return self.dir / "hero.jpg"

    @property
    def pid_file(self) -> Path:
        return self.dir / "running.pid"

    @property
    def install_log(self) -> Path:
        return self.dir / "install.log"

    @property
    def run_log(self) -> Path:
        return self.dir / "last-run.log"

    def save(self) -> None:
        data = asdict(self)
        data.pop("dir")
        tmp = self.dir / (META + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.dir / META)


def _load(d: Path) -> Game | None:
    try:
        data = json.loads((d / META).read_text())
    except (OSError, ValueError):
        return None
    known = Game.__dataclass_fields__.keys() - {"dir"}
    g = Game(**{k: v for k, v in data.items() if k in known})
    g.dir = d
    g.slug = d.name
    return g


def all_games(root: Path | None = None) -> list[Game]:
    root = root or config.games_dir()
    if not root.is_dir():
        return []
    games = [g for d in root.iterdir() if d.is_dir() and (g := _load(d))]
    games.sort(key=lambda g: (-(g.last_played or 0), g.name.lower()))
    return games


def get(slug: str, root: Path | None = None) -> Game | None:
    root = root or config.games_dir()
    return _load(root / slug)


def create(name: str, installer: str, root: Path | None = None) -> Game:
    root = root or config.games_dir()
    root.mkdir(parents=True, exist_ok=True)
    base = slugify(name)
    slug, n = base, 2
    while (root / slug).exists():
        slug, n = f"{base}-{n}", n + 1
    d = root / slug
    d.mkdir()
    g = Game(slug=slug, name=name, installer=str(installer))
    g.dir = d
    g.save()
    return g
