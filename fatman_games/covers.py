"""Jaquettes : cherche le jeu sur la boutique Steam (nom seulement) et garde l'image dans son dossier."""

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .library import Game

UA = {"User-Agent": "fatman-games/1.0"}
SEARCH = "https://store.steampowered.com/api/storesearch/?term={q}&l=french&cc=FR"
CDN = "https://shared.steamstatic.com/store_item_assets/"
ASSETS = CDN + "steam/apps/{id}/{name}"
# Les jeux récents rangent leurs images sous un dossier à empreinte (…/<hash>/library_capsule.jpg) :
# seule l'API de la boutique donne le chemin exact.
ITEMS = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json={q}"
# Portrait d'abord ; à défaut la bannière (recadrée par la carte).
ASSET_KEYS = ["library_capsule_2x", "library_capsule", "header"]
# Anciens jeux : chemins sans empreinte, si l'API ne répond pas.
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


def image_urls(appid: int) -> list[str]:
    """Adresses des images candidates, de la meilleure à la moins bonne."""
    urls = []
    try:
        q = json.dumps({"ids": [{"appid": appid}], "context": {"language": "french", "country_code": "FR"},
                        "data_request": {"include_assets": True}})
        data = json.loads(_get(ITEMS.format(q=urllib.parse.quote(q))))
        assets = data["response"]["store_items"][0].get("assets") or {}
        fmt = assets.get("asset_url_format", "")
        for key in ASSET_KEYS:
            if assets.get(key) and "${FILENAME}" in fmt:
                urls.append(CDN + fmt.replace("${FILENAME}", assets[key]))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return urls + [ASSETS.format(id=appid, name=f) for f in FILES]


def fetch(game: Game) -> bool:
    """Télécharge cover.jpg si possible. Marque le jeu comme vérifié dans tous les cas."""
    ok = False
    try:
        appid = game.steam_appid or find_appid(game.name)
        if appid:
            game.steam_appid = appid
            for url in image_urls(appid):
                try:
                    img = _get(url)
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
