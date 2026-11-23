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


def _assets(appid: int) -> tuple[str, dict]:
    """(asset_url_format, assets) de la boutique Steam, ou ("", {}) sans réponse."""
    try:
        q = json.dumps({"ids": [{"appid": appid}], "context": {"language": "french", "country_code": "FR"},
                        "data_request": {"include_assets": True}})
        data = json.loads(_get(ITEMS.format(q=urllib.parse.quote(q))))
        assets = data["response"]["store_items"][0].get("assets") or {}
        return assets.get("asset_url_format", ""), assets
    except (OSError, ValueError, KeyError, IndexError):
        return "", {}


def _urls(appid: int, keys: list[str], legacy: list[str]) -> list[str]:
    fmt, assets = _assets(appid)
    urls = [CDN + fmt.replace("${FILENAME}", assets[k]) for k in keys
            if assets.get(k) and "${FILENAME}" in fmt]
    return urls + [ASSETS.format(id=appid, name=f) for f in legacy]


def image_urls(appid: int) -> list[str]:
    """Adresses des jaquettes candidates, de la meilleure à la moins bonne."""
    return _urls(appid, ASSET_KEYS, FILES)


def _download(urls: list[str], dest: Path) -> bool:
    for url in urls:
        try:
            img = _get(url)
        except OSError:
            continue
        if img[:3] == b"\xff\xd8\xff":  # vrai JPEG
            tmp = Path(str(dest) + ".tmp")
            tmp.write_bytes(img)
            tmp.replace(dest)
            return True
    return False


def fetch_hero(game: Game) -> bool:
    """Grande illustration horizontale (bibliothèque Steam) pour le bandeau « Reprendre »."""
    if not game.steam_appid:
        return False
    try:
        ok = _download(_urls(game.steam_appid, ["library_hero"], ["library_hero.jpg"]), game.hero)
    except OSError:
        return False
    game.hero_checked = True
    game.save()
    return ok


def fetch(game: Game) -> bool:
    """Télécharge cover.jpg si possible. Marque le jeu comme vérifié dans tous les cas."""
    ok = False
    try:
        appid = game.steam_appid or find_appid(game.name)
        if appid:
            game.steam_appid = appid
            ok = _download(image_urls(appid), game.cover)
    except (OSError, ValueError):
        return False  # pas de réseau : on réessaiera au prochain lancement
    game.cover_checked = True
    game.save()
    return ok
