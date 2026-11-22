import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fatman_games import config, library, repack, saves  # noqa: E402
from fatman_games.detect import dll_overrides, clean_name, find_executables, guess_name, slugify  # noqa: E402


def make_file(p: Path, size: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(size)


class NameTests(unittest.TestCase):
    def test_clean_names(self):
        cases = {
            "Elden Ring - Shadow of the Erdtree Deluxe Edition [Repack]":
                "Elden Ring - Shadow of the Erdtree Deluxe Edition",
            "Cyberpunk 2077 v2.31 + All DLCs + Bonus Content [Repack]": "Cyberpunk 2077",
            "Celeste - SomeGroup Repack": "Celeste",
            "Elden Ring Repack": "Elden Ring",
            "Hollow_Knight_v1.5.78_+_2_DLCs_-_SomeGroup_Repack": "Hollow Knight",
            "Hades II (v1.0.1)": "Hades II",
            "Stardew Valley": "Stardew Valley",
        }
        for raw, want in cases.items():
            self.assertEqual(clean_name(raw), want, raw)

    def test_guess_from_folder_when_setup(self):
        p = Path("/x/Hades II [Repack]/setup.exe")
        self.assertEqual(guess_name(p), "Hades II")

    def test_guess_from_exe_name(self):
        self.assertEqual(guess_name(Path("/dl/Celeste_v1.4.exe")), "Celeste")

    def test_slug(self):
        self.assertEqual(slugify("Elden Ring: Nightreign!"), "elden-ring-nightreign")
        self.assertEqual(slugify("¤¤"), "jeu")


class DetectTests(unittest.TestCase):
    def test_picks_game_over_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / "Games/Hollow Knight"
            make_file(game / "hollow_knight.exe", 650_000)
            make_file(game / "UnityCrashHandler64.exe", 1_400_000)
            make_file(game / "unins000.exe", 3_000_000)
            make_file(game / "_CommonRedist/vcredist_x64.exe", 15_000_000)
            make_file(root / "windows/system32/notepad.exe", 5_000_000)
            make_file(game / "tiny.exe", 10_000)
            c = find_executables(root, "Hollow Knight")
            self.assertEqual([x.path.name for x in c], ["hollow_knight.exe"])

    def test_unreal_shipping_beats_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Games/Stray"
            make_file(root / "Stray.exe", 300_000)
            make_file(root / "Hk_project/Binaries/Win64/Stray-Win64-Shipping.exe", 90_000_000)
            make_file(root / "Engine/Binaries/Win64/CrashReportClient.exe", 30_000_000)
            c = find_executables(Path(tmp), "Stray")
            self.assertEqual(c[0].path.name, "Stray-Win64-Shipping.exe")
            self.assertEqual(len(c), 2)

    def test_name_match_beats_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Games/Celeste"
            make_file(root / "Celeste.exe", 2_000_000)
            make_file(root / "FNA3D_tool.exe", 20_000_000)
            c = find_executables(Path(tmp), "Celeste")
            self.assertEqual(c[0].path.name, "Celeste.exe")


class DllOverrideTests(unittest.TestCase):
    def test_proxy_dlls_next_to_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for f in ("Game.exe", "winmm.dll", "xgameruntime.dll", "dxgi.dll", "Party.dll"):
                make_file(d / f, 10)
            self.assertEqual(dll_overrides(d / "Game.exe"), ["winmm", "xgameruntime"])

    def test_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_file(Path(tmp) / "Game.exe", 10)
            self.assertEqual(dll_overrides(Path(tmp) / "Game.exe"), [])


class RepackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cfg = self.root / "cfg"
        cfg.mkdir()
        (cfg / "config.json").write_text('{"games_dir": "%s"}' % (self.root / "games"))
        self.old = config.CONFIG_FILE
        config.CONFIG_FILE = cfg / "config.json"

    def tearDown(self):
        config.CONFIG_FILE = self.old
        self.tmp.cleanup()

    def test_whole_repack_folder(self):
        d = self.root / "dl/Mixtape [Repack]"
        for f in ("setup.exe", "fg-01.bin", "fg-02.bin", "MD5/bins.md5"):
            make_file(d / f, 1000)
        self.assertEqual(repack.targets(d / "setup.exe"), [d])
        self.assertEqual(repack.size([d]), 4000)
        repack.delete([d])
        self.assertFalse(d.exists())
        self.assertTrue((self.root / "dl").exists())

    def test_loose_in_protected_folder_only_removes_repack_files(self):
        with tempfile.TemporaryDirectory() as t:
            # setup.exe en vrac dans un dossier protégé (ex. Téléchargements) → pas tout le dossier
            d = Path(t)
            for f in ("setup.exe", "fg-01.bin", "photo.jpg"):
                make_file(d / f, 10)
            old = repack._protected
            repack._protected = lambda: {d}
            try:
                got = repack.targets(d / "setup.exe")
            finally:
                repack._protected = old
            self.assertEqual(got, [d / "setup.exe", d / "fg-01.bin"])

    def test_not_a_repack_folder(self):
        d = self.root / "dl/Tools"
        make_file(d / "setup.exe", 10)
        make_file(d / "readme.txt", 10)
        self.assertEqual(repack.targets(d / "setup.exe"), [d / "setup.exe"])

    def test_missing(self):
        self.assertEqual(repack.targets(self.root / "nope/setup.exe"), [])


class SavesTests(unittest.TestCase):
    def test_backup_skips_caches_and_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfgf = root / "config.json"
            cfgf.write_text('{"games_dir": "%s"}' % (root / "games"))
            old, config.CONFIG_FILE = config.CONFIG_FILE, cfgf
            try:
                g = library.create("Jeu", "/x/setup.exe", root / "games")
                prof = g.prefix / "drive_c/users/steamuser"
                make_file(prof / "Documents/My Games/Jeu/save1.sav", 100)
                make_file(prof / "AppData/Local/Jeu/Saved/SaveGames/slot.sav", 50)
                make_file(prof / "AppData/Local/Temp/huge.tmp", 5000)
                make_file(prof / "AppData/Local/D3DSCache/x/cache.bin", 5000)
                arc = saves.backup(g)
                self.assertEqual(saves.backups(g.slug), [arc])
                import tarfile
                names = tarfile.open(arc).getnames()
                self.assertIn("steamuser/Documents/My Games/Jeu/save1.sav", names)
                self.assertFalse(any("Temp" in n or "D3DSCache" in n for n in names))
                import shutil
                shutil.rmtree(prof)
                saves.restore(g, arc)
                self.assertTrue((prof / "AppData/Local/Jeu/Saved/SaveGames/slot.sav").exists())
            finally:
                config.CONFIG_FILE = old


class EnvTests(unittest.TestCase):
    def test_parse_env(self):
        from fatman_games.proton import parse_env
        self.assertEqual(parse_env('DXVK_HUD=fps A="deux mots" junk 1BAD-=x'),
                         {"DXVK_HUD": "fps", "A": "deux mots"})
        self.assertEqual(parse_env('A="pas fermé'), {"A": '"pas'})


class SteamTests(unittest.TestCase):
    def test_vdf_round_trip_and_shortcut(self):
        from fatman_games import steam
        with tempfile.TemporaryDirectory() as tmp:
            ud = Path(tmp) / "userdata/123"
            (ud / "config").mkdir(parents=True)
            old = steam.user_dir
            steam.user_dir = lambda: ud
            try:
                cover = Path(tmp) / "c.jpg"
                cover.write_bytes(b"\xff\xd8\xffjpeg")
                a = steam.add("Jeu A", '"/bin/launcher"', '"/games/a"', cover=cover, launch_options="--play a")
                b = steam.add("Jeu B", '"/bin/launcher2"', '"/games/b"')
                again = steam.add("Jeu A", '"/bin/launcher"', '"/games/a"')
                self.assertEqual(a, again)
                raw = (ud / "config/shortcuts.vdf").read_bytes()
                data = steam.loads(raw)
                self.assertEqual(steam.dumps(data), raw)
                self.assertEqual([v["AppName"] for v in data["shortcuts"].values()], ["Jeu A", "Jeu B"])
                self.assertTrue((ud / f"config/grid/{a}p.jpg").exists())
                self.assertTrue(steam.remove(a))
                data = steam.loads((ud / "config/shortcuts.vdf").read_bytes())
                self.assertEqual(list(data["shortcuts"]), ["0"])
                self.assertEqual(data["shortcuts"]["0"]["appid"], b)
                self.assertFalse((ud / f"config/grid/{a}p.jpg").exists())
            finally:
                steam.user_dir = old


class LibraryTests(unittest.TestCase):
    def test_create_save_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = library.create("Hades II", "/dl/setup.exe", root)
            g2 = library.create("Hades II", "/dl/setup.exe", root)
            self.assertEqual((g.slug, g2.slug), ("hades-ii", "hades-ii-2"))
            g.status, g.exe, g.last_played = library.READY, "pfx/drive_c/Games/Hades II/Hades2.exe", 5
            g.save()
            games = library.all_games(root)
            self.assertEqual([x.slug for x in games], ["hades-ii", "hades-ii-2"])
            got = library.get("hades-ii", root)
            self.assertEqual(got.status, library.READY)
            self.assertEqual(got.exe_path, root / "hades-ii/pfx/drive_c/Games/Hades II/Hades2.exe")

    def test_ignores_foreign_dirs_and_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "other").mkdir()
            (root / "broken").mkdir()
            (root / "broken" / library.META).write_text("{oops")
            self.assertEqual(library.all_games(root), [])


if __name__ == "__main__":
    unittest.main()
