"""Lancer l'installateur ou le jeu dans le préfixe Proton du jeu, via umu-run."""

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import audio, config, library
from .detect import dll_overrides, installer_kind
from .library import Game


def proton_path(game: Game) -> str:
    if game.proton and (Path(game.proton) / "proton").exists():
        return game.proton
    return config.find_proton()


def parse_env(text: str) -> dict:
    """« A=1 B="deux mots" » → {"A": "1", "B": "deux mots"} ; ignore ce qui n'a pas de « = »."""
    out = {}
    try:
        parts = shlex.split(text or "")
    except ValueError:
        parts = (text or "").split()
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            if k.replace("_", "").isalnum():
                out[k] = v
    return out


def environment(game: Game) -> dict:
    env = dict(os.environ)
    env.update({
        "WINEPREFIX": str(game.prefix),
        "PROTONPATH": proton_path(game),
        "GAMEID": "umu-default",
        "STORE": "none",
        "PROTON_LOG": "0",
    })
    return env


def _umu() -> str:
    umu = shutil.which("umu-run")
    if not umu:
        raise RuntimeError("umu-run est introuvable (paquet umu-launcher).")
    return umu


def windows_dir(game: Game) -> str:
    name = "".join(c for c in game.name if c not in '<>:"/\\|?*').strip() or game.slug
    return f"C:\\Games\\{name}"


# Programmes que les repacks lancent en fin d'installation et qu'on empêche de démarrer
# (Wine répond « module introuvable », l'installateur continue).
INSTALLER_EXTRAS = ["quicksfv.exe", "dxwebsetup.exe"]


def _add_overrides(env: dict, rule: str) -> None:
    env["WINEDLLOVERRIDES"] = ";".join(x for x in (env.get("WINEDLLOVERRIDES"), rule) if x)


def installer_environment(game: Game) -> dict:
    env = environment(game)
    cfg = config.load()
    if cfg.get("mute_installer", True):
        audio.tag_env(env, game.slug)  # repérage du flux audio à couper (voir audio.py)
    if cfg.get("block_installer_web", True):
        # Sans winebrowser, « ouvrir le site » en fin d'installation échoue sans rien ouvrir.
        _add_overrides(env, "winebrowser.exe=d")
    if cfg.get("skip_installer_extras", True):
        _add_overrides(env, ",".join(INSTALLER_EXTRAS) + "=d")
    return env


def clean_hosts(game: Game) -> int:
    """Retire du hosts du préfixe les lignes ajoutées par les repacks (blocage de faux sites).

    Ce fichier ne concerne que les programmes Windows du jeu, jamais Linux.
    """
    if not config.load().get("block_installer_web", True):
        return 0
    etc = game.prefix / "drive_c/windows/system32/drivers/etc"
    hosts = etc / "hosts"
    try:
        lines = hosts.read_bytes().decode("utf-8", "replace").splitlines(keepends=True)
    except OSError:
        return 0
    kept = [l for l in lines if "fitgirl" not in l.lower()]
    removed = len(lines) - len(kept)
    if removed:
        hosts.write_text("".join(kept))
    (etc / "hosts.rollback").unlink(missing_ok=True)
    return removed


def install_command(installer: Path, kind: str, target: str, silent: bool) -> list[str]:
    """Arguments passés à umu-run pour cet installateur.

    Inno Setup (repacks) : /VERYSILENT n'affiche rien et prend les options par défaut.
    NSIS : /S, et /D doit être le dernier argument. MSI : msiexec /qn.
    Type inconnu : l'installateur s'ouvre normalement (on propose quand même /DIR, sans effet ailleurs).
    """
    installer = Path(installer)
    if kind == "msi":
        args = ["msiexec", "/i", "Z:" + str(installer.resolve()).replace("/", "\\")]
        if silent:
            args += ["/qn", f"TARGETDIR={target}", f"INSTALLDIR={target}"]
        return args
    if kind == "nsis":
        return [str(installer), *(["/S"] if silent else []), f"/D={target}"]
    args = [str(installer)]
    if silent and kind == "inno":
        args += ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    return args + [f"/DIR={target}"]


def start_installer(game: Game, installer: Path, silent: bool = False) -> subprocess.Popen:
    """Démarre l'installateur dans le préfixe du jeu, dans C:\\Games\\<nom>."""
    game.prefix.mkdir(parents=True, exist_ok=True)
    kind = installer_kind(installer)
    silent = silent and bool(kind)
    args = install_command(installer, kind, windows_dir(game), silent)
    log = open(game.install_log, "ab")
    log.write(f"\n=== Installation {time.ctime()} : {installer}\n".encode())
    log.write(f"=== Type : {kind or 'inconnu'} · mode : {'automatique' if silent else 'fenêtre'}\n".encode())
    log.write(f"=== Arguments : {' '.join(args[1:])}\n".encode())
    if config.load().get("mute_installer", True):
        log.write("=== Musique de l'installateur coupée (sourdine PipeWire)\n".encode())
    if config.load().get("block_installer_web", True):
        log.write("=== Ouverture de pages web bloquée (winebrowser désactivé)\n".encode())
    if config.load().get("skip_installer_extras", True):
        log.write(f"=== Extras de fin d'installation non lancés : {', '.join(INSTALLER_EXTRAS)}\n".encode())
    log.flush()
    return subprocess.Popen(
        [_umu(), *args],
        cwd=str(Path(installer).parent),
        env=installer_environment(game),
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_wineserver(game: Game, timeout: float = 600) -> None:
    """Attend que tout ce qui tourne dans le préfixe soit terminé (décompresseurs en arrière-plan)."""
    ws = Path(proton_path(game)) / "files/bin/wineserver"
    if not ws.exists():
        return
    try:
        subprocess.run([str(ws), "-w"], env=environment(game), timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass


def game_command(game: Game) -> tuple[list[str], dict, list[str]]:
    """Commande, environnement et lignes de journal pour lancer le jeu avec ses réglages."""
    exe = game.exe_path
    if not exe or not exe.exists():
        raise FileNotFoundError(f"Exécutable introuvable : {exe}")
    env = environment(game)
    notes = []
    overrides = dll_overrides(exe)
    if overrides:
        rule = ",".join(overrides) + "=n,b"
        env["WINEDLLOVERRIDES"] = ";".join(x for x in (env.get("WINEDLLOVERRIDES"), rule) if x)
        notes.append(f"DLL du jeu prioritaires sur Wine : {rule}")
    if game.mangohud:
        env["MANGOHUD"] = "1"
        notes.append("MangoHud activé")
    extra = parse_env(game.env)
    if extra:
        env.update(extra)
        notes.append("Variables : " + " ".join(f"{k}={v}" for k, v in extra.items()))
    cmd = [_umu(), str(exe), *shlex.split(game.launch_args or "")]
    if game.gamescope and shutil.which("gamescope"):
        cmd = ["gamescope", *shlex.split(game.gamescope_args or ""), "--", *cmd]
        notes.append(f"Gamescope : {game.gamescope_args}")
    notes.append(f"Proton : {env['PROTONPATH']}")
    return cmd, env, notes


def is_running(game: Game) -> bool:
    try:
        pid = int(game.pid_file.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def play(game: Game) -> int:
    """Lance le jeu et attend sa fin ; met à jour le temps de jeu. Bloquant."""
    cmd, env, notes = game_command(game)
    exe = game.exe_path
    log = open(game.run_log, "wb")
    log.write(f"=== Lancement {time.ctime()} : {exe}\n".encode())
    for n in notes:
        log.write(f"=== {n}\n".encode())
    log.flush()
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=str(exe.parent), env=env, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    game.pid_file.write_text(str(proc.pid))
    fresh = library.get(game.slug, game.dir.parent) or game
    fresh.last_played = started
    fresh.save()
    try:
        rc = proc.wait()
    finally:
        game.pid_file.unlink(missing_ok=True)
        fresh = library.get(game.slug, game.dir.parent) or game  # relu : l'interface a pu l'enregistrer
        fresh.playtime += time.time() - started
        fresh.save()
    return rc


def kill_prefix(game: Game) -> None:
    ws = Path(proton_path(game)) / "files/bin/wineserver"
    if ws.exists():
        subprocess.run([str(ws), "-k"], env=environment(game),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
