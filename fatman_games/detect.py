"""Deviner le nom d'un jeu à partir de l'installateur, et le .exe à lancer après installation."""

import math
import re
from dataclasses import dataclass
from pathlib import Path

# Dossiers d'un préfixe Wine qui ne contiennent jamais le jeu.
EXCLUDED_DIRS = {
    "windows", "programdata", "users", "common files", "internet explorer",
    "windows nt", "windows media player", "windowspowershell", "microsoft",
    "microsoft.net", "reference assemblies", "msbuild", "dotnet",
    "_commonredist", "commonredist", "redist", "redists", "redistributables",
    "_redist", "directx", "vcredist", "dotnetfx", "physx", "prereqs",
    "prerequisites", "_installer", "__installer", "installer", "support",
    "easyanticheat", "battleye", "crashreportclient", "crashpad", "thirdparty",
}

# Noms d'exécutables qui ne lancent pas le jeu.
EXCLUDED_NAME = re.compile(
    r"^(unins\d*|setup.*|install.*|.*redist.*|dxsetup|dxwebsetup|dotnet.*|ndp\d.*|"
    r"oalinst|physx.*|ue\d?prereq.*|uecrash.*|crash.*|.*crashhandler.*|.*crashreport.*|"
    r"quicksfv|unarc|7z.*|zsync.*|notification_helper|.*browsersubprocess.*|"
    r"cefprocess|.*helper|.*_uninstall.*|.*updater.*|.*reporter.*|.*benchmark.*|"
    r"eac.*setup.*|start_protected_game|.*dedicated.*server.*|.*server)$",
    re.IGNORECASE,
)

NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}"            # [Repack], (v1.2), {...}
    r"|\s[-–]\s*\S+\s+repack\b.*$|\brepack\b.*$"  # « - Groupe Repack », « Repack »
    r"|\s[-–+]\s*\d+\s*dlcs?\b.*$|\+\s*.*\bdlcs?\b.*$"  # + 5 DLCs
    r"|\bv\s?\d+(\.\d+)+\S*.*$"                  # v1.2.3 et tout ce qui suit
    r"|\bbuild\s*\d+.*$",
    re.IGNORECASE,
)


def clean_name(raw: str) -> str:
    """« Elden_Ring_-_Deluxe_v1.2.3_+_5_DLCs_[Repack] » → « Elden Ring - Deluxe »."""
    s = raw.replace("_", " ")
    prev = None
    while prev != s:
        prev = s
        s = NOISE.sub("", s).strip()
    s = re.sub(r"\s+", " ", s).strip(" -–+,.")
    return s or raw.strip()


def guess_name(installer: Path) -> str:
    """Un jeu téléchargé est souvent un dossier « Nom du jeu [...] » contenant setup.exe."""
    installer = Path(installer)
    if installer.stem.lower() in {"setup", "install", "installer"}:
        return clean_name(installer.parent.name)
    return clean_name(installer.stem)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "jeu"


@dataclass
class Candidate:
    path: Path
    size: int
    score: float


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 1}


def find_executables(root: Path, game_name: str = "") -> list[Candidate]:
    """Liste les .exe plausibles sous `root`, le plus probable en premier."""
    root = Path(root)
    wanted = _tokens(game_name)
    out: list[Candidate] = []
    for exe in root.rglob("*"):
        if exe.suffix.lower() != ".exe" or not exe.is_file():
            continue
        rel_parts = [p.lower() for p in exe.relative_to(root).parts[:-1]]
        if any(p in EXCLUDED_DIRS for p in rel_parts):
            continue
        if EXCLUDED_NAME.match(exe.stem):
            continue
        size = exe.stat().st_size
        if size < 200_000:  # petits utilitaires, stubs
            continue
        score = math.log2(size)
        stem = exe.stem.lower()
        common = wanted & (_tokens(stem) | _tokens(" ".join(rel_parts[-2:])))
        score += 6 * len(common & _tokens(stem)) + 2 * len(common)
        if stem.endswith("-win64-shipping") or stem.endswith("-wingdk-shipping"):
            score += 8  # vrai binaire d'un jeu Unreal
        if "launcher" in stem:
            score -= 3
        if any(p in {"binaries", "bin", "x64", "win64"} for p in rel_parts):
            score += 1
        score -= 0.5 * len(rel_parts)
        out.append(Candidate(exe, size, score))
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def obvious_exe(cands: list[Candidate], margin: float = 5.0) -> Candidate | None:
    """Le candidat à retenir sans rien demander : seul, ou nettement devant le deuxième."""
    if not cands:
        return None
    if len(cands) == 1 or cands[0].score - cands[1].score >= margin:
        return cands[0]
    return None


def installer_kind(path: Path) -> str:
    """Type d'installateur : 'inno', 'nsis', 'msi', ou '' si inconnu (pas de mode automatique)."""
    path = Path(path)
    if path.suffix.lower() == ".msi":
        return "msi"
    try:
        with open(path, "rb") as f:
            data = f.read(16 << 20)  # le programme d'installation est au début ; les archives suivent
    except OSError:
        return ""
    if not data.startswith(b"MZ"):
        return ""
    if b"JR.Inno.Setup" in data or b"Inno Setup Setup Data" in data or b"InnoSetupLdrWindow" in data:
        return "inno"
    if b"Nullsoft.NSIS" in data or b"NullsoftInst" in data:
        return "nsis"
    return ""


# DLL système que les repacks remplacent souvent par un « chargeur » placé à côté du .exe
# (couche de compatibilité Xbox PC / GDK, mod, injecteur). Wine préfère sa version intégrée : il faut lui dire
# d'utiliser celle du jeu. On n'y met pas dxgi/d3d11/d3d12, qui sont ceux de DXVK/VKD3D.
PROXY_DLLS = {
    "winmm", "version", "dinput8", "winhttp", "xinput1_3", "xinput1_4", "xinput9_1_0",
    "dsound", "xgameruntime", "dbghelp", "msvcp140_atomic_wait",
}


def dll_overrides(exe: Path) -> list[str]:
    """Noms (sans .dll) des DLL du dossier du .exe à charger en priorité sur celles de Wine."""
    try:
        files = {p.name.lower() for p in Path(exe).parent.iterdir() if p.is_file()}
    except OSError:
        return []
    return sorted(n for n in PROXY_DLLS if f"{n}.dll" in files)


def human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return str(n)
