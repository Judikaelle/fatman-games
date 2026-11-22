"""Couper la musique des installateurs (repacks) dans PipeWire / PulseAudio.

Wine ouvre ses flux audio sous le nom du programme (« setup.tmp » pour Inno Setup).
Pendant l'installation, on repère ces flux toutes les demi-secondes et on les met en
sourdine avec pactl. Seuls les flux de l'installateur sont touchés, pas les autres
applications. Repérage : l'étiquette fatman.install posée par PULSE_PROP, ou le nom du
programme d'installation.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path

TAG = "fatman.install"


def installer_names(installer: Path) -> set[str]:
    stem = Path(installer).stem.lower()
    return {Path(installer).name.lower(), f"{stem}.exe", f"{stem}.tmp", "setup.tmp"}


def tag_env(env: dict, slug: str) -> dict:
    """Ajoute l'étiquette aux propriétés PulseAudio du client (lue par libpulse)."""
    prop = f"{TAG}={slug}"
    env["PULSE_PROP"] = " ".join(x for x in (env.get("PULSE_PROP"), prop) if x)
    return env


def _list(kind: str) -> list[dict]:
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", kind], capture_output=True,
                             text=True, timeout=5).stdout
        return json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def to_mute(streams: list[dict], slug: str, names: set[str]) -> list[int]:
    """Index des flux de l'installateur encore audibles."""
    out = []
    for s in streams:
        props = s.get("properties") or {}
        ours = props.get(TAG) == slug or str(props.get("application.name", "")).lower() in names
        if ours and not s.get("mute"):
            out.append(s["index"])
    return out


def mute_now(slug: str, names: set[str]) -> int:
    ids = to_mute(_list("sink-inputs"), slug, names)
    for i in ids:
        subprocess.run(["pactl", "set-sink-input-mute", str(i), "1"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return len(ids)


class Muter:
    """Tourne en arrière-plan jusqu'à stop() ; ne fait rien sans pactl."""

    def __init__(self, slug: str, installer: Path, interval: float = 0.5):
        self.slug, self.names, self.interval = slug, installer_names(installer), interval
        self.muted = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> "Muter":
        if shutil.which("pactl"):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.muted += mute_now(self.slug, self.names)
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
