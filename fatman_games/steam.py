"""Ajouter un jeu à Steam comme « jeu non-Steam » (shortcuts.vdf binaire + jaquette).

Steam relit shortcuts.vdf à son démarrage et le réécrit quand il change ses raccourcis :
il faut donc qu'il soit fermé pendant l'écriture (l'interface s'en charge).
"""

import os
import re
import shutil
import struct
import subprocess
import time
import zlib
from pathlib import Path

STEAM_DIRS = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
STEAMID64_BASE = 76561197960265728

# ------------------------------------------------------------ VDF binaire

T_MAP, T_STR, T_INT, T_END = 0x00, 0x01, 0x02, 0x08


def _cstr(b: bytes, i: int) -> tuple[str, int]:
    j = b.index(b"\x00", i)
    return b[i:j].decode("utf-8", "replace"), j + 1


def _parse_map(b: bytes, i: int) -> tuple[dict, int]:
    out = {}
    while True:
        t = b[i]
        i += 1
        if t == T_END:
            return out, i
        key, i = _cstr(b, i)
        if t == T_MAP:
            out[key], i = _parse_map(b, i)
        elif t == T_STR:
            out[key], i = _cstr(b, i)
        elif t == T_INT:
            out[key] = struct.unpack_from("<I", b, i)[0]
            i += 4
        else:
            raise ValueError(f"type VDF inconnu {t:#x} à l'octet {i - 1}")


def loads(b: bytes) -> dict:
    data, i = _parse_map(b, 0)
    return data


def _dump_map(d: dict) -> bytes:
    out = bytearray()
    for k, v in d.items():
        kb = k.encode() + b"\x00"
        if isinstance(v, dict):
            out += bytes([T_MAP]) + kb + _dump_map(v)
        elif isinstance(v, int):
            out += bytes([T_INT]) + kb + struct.pack("<I", v & 0xFFFFFFFF)
        else:
            out += bytes([T_STR]) + kb + str(v).encode() + b"\x00"
    out.append(T_END)
    return bytes(out)


def dumps(d: dict) -> bytes:
    return _dump_map(d)


# ------------------------------------------------------------ Steam local

def steam_root() -> Path | None:
    for d in STEAM_DIRS:
        if (d / "userdata").is_dir():
            return d
    return None


def user_dir() -> Path | None:
    """Dossier userdata/<id> du dernier compte connecté."""
    root = steam_root()
    if not root:
        return None
    try:
        text = (root / "config/loginusers.vdf").read_text(errors="replace")
        blocks = re.findall(r'"(\d{17})"\s*\{([^}]*)\}', text)
        best = next((sid for sid, body in blocks if re.search(r'"MostRecent"\s*"1"', body)), None)
        best = best or (blocks[0][0] if blocks else None)
        if best:
            d = root / "userdata" / str(int(best) - STEAMID64_BASE)
            if d.is_dir():
                return d
    except OSError:
        pass
    dirs = [d for d in (root / "userdata").iterdir() if d.is_dir() and d.name != "0"]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def shortcut_appid(exe: str, name: str) -> int:
    """Identifiant que Steam donne à un raccourci (sert aussi au nom des jaquettes)."""
    return (zlib.crc32((exe + name).encode()) & 0xFFFFFFFF) | 0x80000000


def is_running() -> bool:
    return subprocess.run(["pgrep", "-x", "steam"], stdout=subprocess.DEVNULL).returncode == 0


def shutdown(timeout: float = 40) -> bool:
    if not is_running():
        return True
    subprocess.Popen(["steam", "-shutdown"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    end = time.time() + timeout
    while time.time() < end:
        if not is_running():
            time.sleep(1)
            return True
        time.sleep(1)
    return False


def start() -> None:
    subprocess.Popen(["steam", "-silent"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _load(path: Path) -> dict:
    if path.exists() and path.stat().st_size:
        return loads(path.read_bytes())
    return {"shortcuts": {}}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name("shortcuts.vdf.fatman-bak")
        if not backup.exists():
            shutil.copy2(path, backup)  # copie d'origine, gardée une seule fois
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(dumps(data))
    os.replace(tmp, path)


def add(name: str, exe: str, start_dir: str, icon: str = "", cover: Path | None = None,
        launch_options: str = "") -> int:
    """Ajoute (ou met à jour) le raccourci. Renvoie son appid. Steam doit être fermé."""
    ud = user_dir()
    if not ud:
        raise RuntimeError("Aucun compte Steam trouvé sur ce PC.")
    path = ud / "config/shortcuts.vdf"
    data = _load(path)
    sc = data.setdefault("shortcuts", {})
    appid = shortcut_appid(exe, name)
    entry = {
        "appid": appid, "AppName": name, "Exe": exe, "StartDir": start_dir, "icon": icon,
        "ShortcutPath": "", "LaunchOptions": launch_options, "IsHidden": 0,
        "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0, "Devkit": 0,
        "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0,
        "FlatpakAppID": "", "sortas": "", "tags": {},
    }
    key = next((k for k, v in sc.items() if isinstance(v, dict) and v.get("appid") == appid), None)
    if key is None:
        key = str(max((int(k) for k in sc if k.isdigit()), default=-1) + 1)
    sc[key] = entry
    _save(path, data)
    if cover and cover.exists():
        grid = ud / "config/grid"
        grid.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cover, grid / f"{appid}p.jpg")   # jaquette verticale de la bibliothèque
    return appid


def remove(appid: int) -> bool:
    ud = user_dir()
    if not ud:
        return False
    path = ud / "config/shortcuts.vdf"
    if not path.exists():
        return False
    data = _load(path)
    sc = data.get("shortcuts", {})
    kept = [v for v in sc.values() if not (isinstance(v, dict) and v.get("appid") == appid)]
    if len(kept) == len(sc):
        return False
    data["shortcuts"] = {str(i): v for i, v in enumerate(kept)}
    _save(path, data)
    (ud / f"config/grid/{appid}p.jpg").unlink(missing_ok=True)
    return True
