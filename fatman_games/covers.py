"""Jaquettes : cherche le jeu sur la boutique Steam (nom seulement) et garde l'image dans son dossier."""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .library import Game

UA = {"User-Agent": "fatman-games/1.0"}
SEARCH = "https://store.steampowered.com/api/storesearch/?term={q}&l=french&cc=FR"
ASSETS = "https://shared.steamstatic.com/store_item_assets/steam/apps/{id}/{name}"
# Portrait d'abord ; à défaut la bannière (recadrée par la carte).
FILES = ["library_600x900_2x.jpg", "library_600x900.jpg", "header.jpg"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _get(url: str, timeout: float = 10) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def find_appid(name: str) -> int:
    data = json.loads(_get(SEARCH.format(q=urllib.parse.quote(name))))
    items = data.get("items") or []
    want = _norm(name)
    for it in items:                       # nom identique
        if _norm(it.get("name", "")) == want:
            return int(it["id"])
    for it in items:                       # « Nom: Édition Deluxe » commence par le nom voulu
        if _norm(it.get("name", "")).startswith(want + " "):
            return int(it["id"])
    return 0


def fetch(game: Game) -> bool:
    """Télécharge cover.jpg si possible. Marque le jeu comme vérifié dans tous les cas."""
    ok = False
    try:
        appid = game.steam_appid or find_appid(game.name)
        if appid:
            game.steam_appid = appid
            for f in FILES:
                try:
                    img = _get(ASSETS.format(id=appid, name=f))
                except OSError:
                    continue
                if img[:3] == b"\xff\xd8\xff":  # vrai JPEG
                    tmp = Path(str(game.cover) + ".tmp")
                    tmp.write_bytes(img)
                    tmp.replace(game.cover)
                    ok = True
                    break
    except (OSError, ValueError):
        return False  # pas de réseau : on réessaiera au prochain lancement
    game.cover_checked = True
    game.save()
    return ok
