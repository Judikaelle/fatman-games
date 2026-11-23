"""Fenêtre GTK4 / libadwaita : glisser un setup.exe, suivre l'installation, lancer les jeux."""

import shutil
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import APP_ID, APP_NAME, audio, config, covers, desktop, library, proton, repack, saves, steam  # noqa: E402
from .detect import find_executables, guess_name, human_size, installer_kind, obvious_exe  # noqa: E402

MAX_CANDIDATES = 15
# Installation automatique cachée : arrêtée si rien n'est écrit pendant ce temps.
STALL_SECONDS = 600


def _date(ts: float) -> str:
    return time.strftime("%d/%m/%Y", time.localtime(ts)) if ts else ""


MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]


def _since(ts: float, now: float | None = None) -> str:
    """« aujourd'hui », « hier », « il y a 3 jours », « le 4 novembre » (année si différente)."""
    now = now or time.time()
    day = time.localtime(ts)
    today = time.localtime(now)
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(day[:3] + (0, 0, 0, 0, 0, -1)))
    days = round(days / 86400)
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    if days < 7:
        return f"il y a {days} jours"
    text = f"le {day.tm_mday} {MONTHS[day.tm_mon - 1]}"
    return text if day.tm_year == today.tm_year else f"{text} {day.tm_year}"


def _duration(sec: float) -> str:
    m = int(sec // 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d}"


CSS = b"""
.library { padding: 24px 32px 32px 32px; }
.hero { border-radius: 20px; box-shadow: 0 12px 32px alpha(black, 0.35); }
.hero-scrim {
  background: linear-gradient(to top, alpha(black, 0.88), alpha(black, 0.45) 45%, alpha(black, 0) 80%);
}
.hero-kicker { color: alpha(white, 0.75); font-size: 10.5pt; font-weight: 600; }
.hero-title { color: white; font-size: 26pt; font-weight: 800; letter-spacing: -0.5px; }
.hero-sub { color: alpha(white, 0.8); font-size: 10.5pt; }
.hero-play { border-radius: 999px; font-weight: 800; font-size: 12pt; padding: 10px 28px; }
.section-title { font-size: 13pt; font-weight: 800; margin-top: 28px; margin-bottom: 14px; }
.section-count { font-size: 10.5pt; opacity: 0.55; margin-top: 28px; margin-bottom: 14px; }
.game-grid > flowboxchild,
.game-grid > flowboxchild:hover,
.game-grid > flowboxchild:active,
.game-grid > flowboxchild:focus,
.game-grid > flowboxchild:focus-visible,
.game-grid > flowboxchild:selected {
  background: none;
  box-shadow: none;
  outline: none;
  padding: 0;
}
.game-card {
  background: alpha(@card_bg_color, 0.85);
  border-radius: 16px;
  padding: 0 0 12px 0;
  box-shadow: 0 1px 3px alpha(black, 0.25);
  transition: transform 160ms ease, box-shadow 160ms ease;
}
.game-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 28px alpha(black, 0.45);
}
.cover { border-radius: 16px 16px 0 0; }
.cover-fallback {
  border-radius: 16px 16px 0 0;
  background: linear-gradient(160deg, #e8507a, #5b2a86);
}
.cover-fallback label { color: white; font-weight: 800; font-size: 15pt; }
.card-title { font-weight: 700; font-size: 10.5pt; }
.card-sub { font-size: 9pt; opacity: 0.65; }
.badge {
  background: alpha(black, 0.65); color: white;
  border-radius: 999px; padding: 4px 10px; font-size: 9pt; font-weight: 700;
}
.badge.warn { background: alpha(#c64600, 0.9); }
.badge.live { background: alpha(#26a269, 0.92); }
.badge.error { background: alpha(#c01c28, 0.92); }
.card-menu { min-width: 24px; min-height: 24px; padding: 0; }
.play-btn { border-radius: 999px; font-weight: 700; }
.card-play { background: alpha(currentColor, 0.1); }
.card-play:hover { background: @accent_bg_color; color: @accent_fg_color; }
"""

CARD_W, CARD_H = 200, 300


def _hero_texture(g: library.Game, scale: int = 1):
    """Grande illustration Steam ; à défaut la jaquette floutée (Pillow) ; sinon rien."""
    if g.hero.exists():
        try:
            return Gdk.Texture.new_from_filename(str(g.hero))
        except GLib.Error:
            pass
    if not g.cover.exists():
        return None
    try:
        import io
        from PIL import Image, ImageFilter
        im = Image.open(g.cover).convert("RGB")
        w = 1280
        h = round(w * im.height / im.width)
        im = im.resize((w, h)).crop((0, h // 4, w, h // 4 + 420)).filter(ImageFilter.GaussianBlur(28))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))
    except Exception:  # Pillow absent ou image illisible : pas de bandeau
        return None


def _cover_texture(path: Path, scale: int = 1):
    """Jaquette recadrée et réduite à la taille exacte de la carte (sinon elle étire la grille)."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except GLib.Error:
        return None
    w, h = CARD_W * scale, CARD_H * scale
    k = max(w / pb.get_width(), h / pb.get_height())
    sw, sh = max(w, round(pb.get_width() * k)), max(h, round(pb.get_height() * k))
    pb = pb.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
    pb = pb.new_subpixbuf((sw - w) // 2, (sh - h) // 2, w, h)
    return Gdk.Texture.new_for_pixbuf(pb)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1100, 760)
        self.installing: dict[str, object] = {}   # slug -> Popen de l'installateur
        self.running: set[str] = set()            # jeux lancés (par l'appli ou le menu K)
        self.progress: dict[str, tuple] = {}      # slug -> (barre, étiquette, octets, instant)
        self.stall: dict[str, tuple] = {}         # slug -> (octets, depuis quand ils n'augmentent plus)
        self.busy: set[str] = set()               # désinstallation / recherche en cours
        self.fetching = False
        self.fresh: set[str] = set()              # installés à l'instant : proposer de supprimer l'installateur

        css = Gtk.CssProvider()
        css.load_from_data(CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button()
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Installer un jeu"))
        add.add_css_class("suggested-action")
        add.connect("clicked", self._pick_installer)
        header.pack_start(add)
        self.search = Gtk.SearchEntry(placeholder_text="Rechercher un jeu", width_chars=28)
        self.search.connect("search-changed", self._on_search)
        header.set_title_widget(self.search)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Actualiser")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)
        view.add_top_bar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage(
            icon_name="applications-games-symbolic",
            title="Aucun jeu installé",
            description="Glisse un setup.exe dans cette fenêtre,\nou clique sur « Installer un jeu ».",
        )
        self.stack.add_named(self.empty, "empty")
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                activate_on_single_click=False,
                                valign=Gtk.Align.START, halign=Gtk.Align.START, column_spacing=45, row_spacing=26,
                                min_children_per_line=2, max_children_per_line=12)
        self.grid.add_css_class("game-grid")
        self.grid.set_filter_func(self._filter)
        # Bandeau « Reprendre » (dernier jeu lancé), puis toute la bibliothèque.
        self.hero_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.section = Gtk.Box(spacing=10)
        self.section.append(Gtk.Label(label="Bibliothèque", xalign=0, css_classes=["section-title"]))
        self.count = Gtk.Label(xalign=0, css_classes=["section-count"])
        self.section.append(self.count)
        lib = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["library"])
        lib.append(self.hero_slot)
        lib.append(self.section)
        lib.append(self.grid)
        self.stack.add_named(Gtk.ScrolledWindow(child=Adw.Clamp(child=lib, maximum_size=1280,
                                                                tightening_threshold=1280),
                                                vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        view.set_content(self.stack)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        for name, cb in [
            ("play", self._play), ("change-exe", self._change_exe), ("open-dir", self._open_dir),
            ("install-log", self._install_log), ("run-log", self._run_log),
            ("stop", self._stop), ("uninstall", self._uninstall),
            ("delete-repack", self._delete_repack), ("settings", self._settings),
            ("steam", self._steam_toggle), ("retry-install", self._retry_install), ("backup", self._backup), ("restore", self._restore),
        ]:
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda a, p, cb=cb: cb(p.get_string()))
            self.add_action(act)

        GLib.timeout_add_seconds(3, self._poll_running)
        GLib.timeout_add_seconds(2, self._poll_progress)
        self.refresh()

    # ---------------------------------------------------------------- liste

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def _filter(self, child) -> bool:
        q = self.search.get_text().strip().lower()
        return not q or q in getattr(child, "game_name", "").lower()

    def refresh(self) -> None:
        self.grid.remove_all()
        root = config.games_dir()
        if not root.parent.exists():
            self.empty.set_title("Disque des jeux introuvable")
            self.empty.set_description(f"{root.parent} n'est pas monté.")
            self.stack.set_visible_child_name("empty")
            return
        games = library.all_games(root)
        for g in games:
            child = Gtk.FlowBoxChild(child=self._card(g), focusable=False)
            child.game_name = g.name
            self.grid.append(child)
        # Largeur naturelle = autant de cartes que de jeux : la grille reste serrée.
        self.grid.set_max_children_per_line(max(2, min(12, len(games))))
        self._update_count()
        self._show_hero(games)
        self.stack.set_visible_child_name("list" if games else "empty")
        self._fetch_covers([g for g in games if (not g.cover_checked and not g.cover.exists())
                            or (not g.hero_checked and g.steam_appid)])

    def _on_search(self, *_):
        self.grid.invalidate_filter()
        q = self.search.get_text().strip().lower()
        self.hero_slot.set_visible(not q and self.hero_slot.get_first_child() is not None)
        self._update_count()

    def _update_count(self) -> None:
        q = self.search.get_text().strip().lower()
        names = []
        child = self.grid.get_first_child()
        while child:
            names.append(getattr(child, "game_name", ""))
            child = child.get_next_sibling()
        if q:
            n = sum(1 for name in names if q in name.lower())
            self.count.set_label("aucun résultat" if n == 0 else f"{n} résultat" + ("s" if n > 1 else ""))
        else:
            self.count.set_label(f"{len(names)} jeu" + ("x" if len(names) > 1 else ""))

    def _hero_game(self, games):
        ready = [g for g in games if g.status == library.READY]
        played = [g for g in ready if g.last_played]
        if played:
            return max(played, key=lambda g: g.last_played), "Reprendre"
        if ready:
            return max(ready, key=lambda g: g.installed_at), "Prêt à jouer"
        return None, ""

    def _show_hero(self, games) -> None:
        while (c := self.hero_slot.get_first_child()):
            self.hero_slot.remove(c)
        g, kicker = self._hero_game(games)
        tex = _hero_texture(g, self.get_scale_factor()) if g else None
        self.hero_slot.set_visible(bool(tex) and not self.search.get_text().strip())
        if not tex:
            return
        pic = Gtk.Picture(paintable=tex, can_shrink=True, content_fit=Gtk.ContentFit.COVER, height_request=260)
        overlay = Gtk.Overlay(child=pic, css_classes=["hero"], overflow=Gtk.Overflow.HIDDEN)
        overlay.add_overlay(Gtk.Box(css_classes=["hero-scrim"]))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.END,
                       halign=Gtk.Align.START, margin_start=32, margin_bottom=28, margin_end=32)
        running = g.slug in self.running
        info.append(Gtk.Label(label="En cours de jeu" if running else kicker, xalign=0,
                              css_classes=["hero-kicker"]))
        info.append(Gtk.Label(label=g.name, xalign=0, wrap=True, css_classes=["hero-title"]))
        facts = []
        if g.last_played:
            facts.append(f"Dernière partie {_since(g.last_played)}")
        if g.playtime >= 60:
            facts.append(f"{_duration(g.playtime)} de jeu au total")
        if facts:
            info.append(Gtk.Label(label=", ".join(facts) if len(facts) == 1
                                  else f"{facts[0]}, {facts[1][0].lower()}{facts[1][1:]}",
                                  xalign=0, css_classes=["hero-sub"]))
        target = GLib.Variant("s", g.slug)
        btn = Gtk.Button(action_name="win.stop" if running else "win.play", action_target=target,
                         halign=Gtk.Align.START, margin_top=14)
        btn.set_child(Adw.ButtonContent(label="Arrêter" if running else "Jouer",
                                        icon_name="media-playback-stop-symbolic" if running
                                        else "media-playback-start-symbolic"))
        btn.add_css_class("destructive-action" if running else "suggested-action")
        btn.add_css_class("hero-play")
        info.append(btn)
        overlay.add_overlay(info)
        self.hero_slot.append(overlay)

    def _fetch_covers(self, todo) -> None:
        if not todo or self.fetching:
            return
        self.fetching = True

        def work():
            got = 0
            for g in todo:
                if not g.cover_checked and not g.cover.exists():
                    got += covers.fetch(g)
                if not g.hero_checked and g.steam_appid:
                    got += covers.fetch_hero(g)
            GLib.idle_add(done, got)

        def done(got):
            self.fetching = False
            if got:
                self.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()

    def _cover(self, g: library.Game) -> Gtk.Widget:
        tex = _cover_texture(g.cover, self.get_scale_factor()) if g.cover.exists() else None
        if tex:
            pic = Gtk.Picture(paintable=tex, can_shrink=True, content_fit=Gtk.ContentFit.COVER)
            pic.add_css_class("cover")
            pic.set_size_request(CARD_W, CARD_H)
            return pic
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      valign=Gtk.Align.FILL, halign=Gtk.Align.FILL)
        box.add_css_class("cover-fallback")
        box.set_size_request(CARD_W, CARD_H)
        img = (Gtk.Image.new_from_file(str(g.icon)) if g.icon.exists()
               else Gtk.Image.new_from_icon_name("applications-games-symbolic"))
        img.set_pixel_size(96)
        img.set_vexpand(True)
        img.set_valign(Gtk.Align.END)
        box.append(img)
        lbl = Gtk.Label(label=g.name, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=12,
                        vexpand=True, valign=Gtk.Align.START, margin_start=12, margin_end=12)
        box.append(lbl)
        return box

    def _card(self, g: library.Game) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=CARD_W,
                       halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        card.add_css_class("game-card")
        card.set_overflow(Gtk.Overflow.HIDDEN)

        overlay = Gtk.Overlay(child=self._cover(g))
        card.append(overlay)

        def badge(text, kind=None, spinner=False):
            # En bas de la jaquette : le haut porte souvent le logo du jeu.
            b = Gtk.Box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.END,
                        margin_bottom=10, margin_start=10)
            b.add_css_class("badge")
            if kind:
                b.add_css_class(kind)
            if spinner:
                b.append(Adw.Spinner())
            b.append(Gtk.Label(label=text))
            overlay.add_overlay(b)

        target = GLib.Variant("s", g.slug)
        menu = Gio.Menu()
        sections = [
            [("Réglages…", "settings"), ("Changer le .exe…", "change-exe"),
             ("Retirer de Steam" if g.steam_shortcut else "Ajouter à Steam", "steam")],
            [("Sauvegarder les saves", "backup"), ("Restaurer des saves…", "restore")],
            [("Ouvrir le dossier", "open-dir"), ("Journal d'installation", "install-log"),
             ("Journal du dernier lancement", "run-log")],
            [("Forcer l'arrêt", "stop"), ("Supprimer l'installateur…", "delete-repack"),
             ("Désinstaller…", "uninstall")],
        ]
        for entries in sections:
            sec = Gio.Menu()
            for label, action in entries:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(f"win.{action}", target)
                sec.append_item(item)
            menu.append_section(None, sec)
        # Le menu va à côté du nom, sous la jaquette, pour ne rien cacher de l'image.
        mb = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                            tooltip_text="Plus d'actions")
        mb.add_css_class("flat")
        mb.add_css_class("circular")
        mb.add_css_class("card-menu")

        title = Gtk.Label(label=g.name, xalign=0, ellipsize=3, tooltip_text=g.name,
                          max_width_chars=1, hexpand=True, wrap=True, lines=2,
                          wrap_mode=Pango.WrapMode.WORD_CHAR)
        title.add_css_class("card-title")
        head = Gtk.Box(spacing=2, margin_start=12, margin_end=6, margin_top=4)
        head.append(title)
        head.append(mb)
        card.append(head)
        sub = Gtk.Label(xalign=0, ellipsize=3, margin_start=14, margin_end=14, max_width_chars=1)
        sub.add_css_class("card-sub")
        card.append(sub)

        def button(label, action, css="suggested-action", icon=None):
            b = Gtk.Button(action_name=f"win.{action}", action_target=target,
                           margin_start=12, margin_end=12, margin_top=4)
            b.set_child(Adw.ButtonContent(label=label, icon_name=icon or "", can_shrink=True))
            b.add_css_class(css)
            b.add_css_class("play-btn")
            card.append(b)

        if g.slug in self.busy:
            badge("Patiente…", spinner=True)
            sub.set_label("Opération en cours")
        elif g.status == library.INSTALLING and g.slug in self.installing:
            badge("Installation", spinner=True)
            sub.set_label("Installation automatique…" if g.install_mode == "auto"
                          else "Suis l'installateur Windows")
            bar = Gtk.ProgressBar(margin_start=14, margin_end=14, margin_top=2, pulse_step=0.08)
            card.append(bar)
            old = self.progress.get(g.slug)
            self.progress[g.slug] = (bar, sub, old[2] if old else 0, old[3] if old else time.time())
            if old and old[2]:
                sub.set_label(f"{human_size(old[2])} écrits")
        elif g.status == library.FAILED:
            badge("Échec", "error")
            sub.set_label("Installation automatique échouée")
            sub.set_tooltip_text("Voir « Journal d'installation » dans le menu ⋮")
            button("Réessayer avec la fenêtre", "retry-install", icon="view-refresh-symbolic")
        elif g.status in (library.INSTALLING, library.NEEDS_EXE):
            badge("À configurer", "warn")
            sub.set_label("Installation interrompue" if g.status == library.INSTALLING
                          else "Choisis le fichier du jeu")
            button("Choisir le .exe", "change-exe", icon="document-open-symbolic")
        elif g.slug in self.running:
            badge("En cours", "live")
            sub.set_label(f"{_duration(g.playtime)} de jeu" if g.playtime >= 60 else "En cours de jeu")
            button("Arrêter", "stop", "destructive-action", icon="media-playback-stop-symbolic")
        else:
            sub.set_label(f"Joué {_since(g.last_played)}" if g.last_played else "Jamais lancé")
            tip = [f"Installé le {_date(g.installed_at)}"]
            if g.playtime >= 60:
                tip.insert(0, f"{_duration(g.playtime)} de jeu")
            sub.set_tooltip_text("\n".join(tip))
            # Bouton neutre : l'accent bleu est réservé au bandeau et aux actions à faire.
            button("Jouer", "play", css="card-play", icon="media-playback-start-symbolic")

        # Double-clic sur la jaquette = Jouer
        if g.status == library.READY and g.slug not in self.running:
            click = Gtk.GestureClick()
            click.connect("released", lambda gst, n, x, y: n == 2 and self._play(g.slug))
            overlay.add_controller(click)
        return card

    # ---------------------------------------------------------- installation

    def _on_drop(self, _target, value, _x, _y):
        exes = [Path(f.get_path()) for f in value.get_files()
                if f.get_path() and f.get_path().lower().endswith(".exe")]
        if not exes:
            self.toast("Dépose un fichier .exe (le setup.exe du jeu).")
            return False
        for exe in exes:
            self.start_install(exe)
        return True

    def _pick_installer(self, *_):
        dlg = Gtk.FileDialog(title="Choisis le setup.exe")
        f = Gtk.FileFilter(name="Installateurs Windows (*.exe)")
        f.add_suffix("exe")
        f.add_suffix("EXE")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)
        dlg.set_initial_folder(Gio.File.new_for_path(str(Path.home() / "Téléchargements"))
                               if (Path.home() / "Téléchargements").is_dir() else None)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except GLib.Error:
                return
            if file and file.get_path():
                self.start_install(Path(file.get_path()))
        dlg.open(self, None, done)

    def start_install(self, installer: Path) -> None:
        if not installer.is_file():
            self.toast(f"Fichier introuvable : {installer}")
            return
        cfg = config.load()
        kind = installer_kind(installer)
        entry = Gtk.Entry(text=guess_name(installer), activates_default=True)
        source = (installer.parent.name if installer.stem.lower() in {"setup", "install", "installer"}
                  else installer.name)
        dlg = Adw.AlertDialog(heading="Installer un jeu", body=f"{source}\n\nNom du jeu dans ta liste :")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(entry)
        opts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        opts.add_css_class("boxed-list")
        auto = Adw.SwitchRow(title="Installation automatique")
        if kind:
            auto.set_active(bool(cfg.get("auto_install", True)))
            auto.set_subtitle("Aucune fenêtre Windows : options par défaut de l'installateur "
                              "(en général tout le jeu et ses DLC)")
        else:
            auto.set_active(False)
            auto.set_sensitive(False)
            auto.set_subtitle("Pas prise en charge par cet installateur : il va s'ouvrir")
        opts.append(auto)
        delete = Adw.SwitchRow(title="Supprimer l'installateur à la fin",
                               subtitle="Une fois le jeu prêt. Définitif, sans corbeille",
                               active=bool(cfg.get("delete_installer", False)))
        opts.append(delete)
        box.append(opts)
        hint = Gtk.Label(wrap=True, xalign=0)
        hint.add_css_class("dim-label")
        box.append(hint)

        def update_hint(*_):
            hint.set_label(
                "Tu recevras une notification quand le jeu sera prêt." if auto.get_active() else
                "L'installateur Windows va s'ouvrir. Garde le dossier qu'il propose "
                "(C:\\Games\\…) et coche ce que tu veux, puis attends la fin.")
        auto.connect("notify::active", update_hint)
        update_hint()
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Annuler")
        dlg.add_response("install", "Installer")
        dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("install")
        dlg.set_close_response("cancel")

        def on_response(_d, resp):
            name = entry.get_text().strip()
            if resp != "install" or not name:
                return
            cfg = config.load()
            if kind:
                cfg["auto_install"] = auto.get_active()
            cfg["delete_installer"] = delete.get_active()
            try:
                config.save(cfg)
            except OSError:
                pass
            try:
                g = library.create(name, str(installer))
            except OSError as e:
                self.toast(f"Impossible de créer le dossier du jeu : {e}")
                return
            g.delete_installer = delete.get_active()
            self._run_installer(g, installer, auto.get_active())
        dlg.connect("response", on_response)
        dlg.present(self)

    def _run_installer(self, g: library.Game, installer: Path, silent: bool) -> None:
        silent = silent and bool(installer_kind(installer))
        g.install_mode = "auto" if silent else "window"
        g.status = library.INSTALLING
        try:
            g.save()
            proc = proton.start_installer(g, installer, silent)
        except (OSError, RuntimeError) as e:
            self.toast(f"Impossible de lancer l'installation : {e}")
            return
        self.installing[g.slug] = proc
        self.progress[g.slug] = (None, None, 0, time.time())
        self.stall[g.slug] = (0, time.time())
        self.refresh()
        first = "" if (g.prefix / "drive_c").is_dir() else " La première fois, Proton prépare l'environnement (une minute)."
        self.toast((f"Installation automatique de {g.name}, sans fenêtre." if silent
                    else f"Installation de {g.name} : l'installateur s'ouvre.") + first)

        muter = audio.Muter(g.slug, installer).start() if config.load().get("mute_installer", True) else None

        def wait():
            rc = proc.wait()
            proton.wait_wineserver(g)
            if muter:
                muter.stop()
            removed = proton.clean_hosts(g)
            if removed:
                with open(g.install_log, "a") as log:
                    log.write(f"=== {removed} ligne(s) ajoutée(s) au hosts du préfixe par l'installateur : retirées\n")
            cands = find_executables(g.prefix / "drive_c", g.name)
            GLib.idle_add(self._installer_done, g.slug, rc, cands)
        threading.Thread(target=wait, daemon=True).start()

    def _retry_install(self, slug: str) -> None:
        g = library.get(slug)
        if not g or slug in self.installing:
            return
        installer = Path(g.installer) if g.installer else None
        if not installer or not installer.is_file():
            self.toast("L'installateur n'est plus là : retélécharge-le, ou choisis le .exe à la main.")
            return
        proton.kill_prefix(g)
        self._run_installer(g, installer, silent=False)

    def _installer_done(self, slug, rc, cands):
        self.installing.pop(slug, None)
        self.progress.pop(slug, None)
        self.stall.pop(slug, None)
        g = library.get(slug)
        if not g:
            return False
        auto = g.install_mode == "auto"
        icon = str(g.icon) if g.icon.exists() else ""
        with open(g.install_log, "a") as log:
            log.write(f"=== Fin : code {rc}, {len(cands)} exécutable(s) trouvé(s)\n")
        if not cands:
            g.status = library.FAILED if auto else library.NEEDS_EXE
            g.save()
            self.refresh()
            desktop.notify(f"{g.name} : aucun jeu trouvé",
                           "L'installation automatique a échoué : réessaie avec la fenêtre."
                           if auto else "L'installation a peut-être été annulée.")
            self.toast(f"{g.name} : aucun jeu trouvé (code {rc}). Voir le journal d'installation.")
            return False
        g.status = library.NEEDS_EXE
        g.save()
        self.fresh.add(slug)
        best = obvious_exe(cands) if auto and rc == 0 else None
        if best:
            self.set_exe(g, best.path)
            desktop.notify(f"{g.name} est prêt", f"Installé automatiquement ({best.path.name}).",
                           str(g.icon) if g.icon.exists() else icon)
            return False
        desktop.notify(f"{g.name} : installation terminée", "Choisis le fichier du jeu pour finir.", icon)
        self.refresh()
        self._choose_exe_dialog(g, cands)
        return False

    # ------------------------------------------------------------ choix .exe

    def _change_exe(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        self.busy.add(slug)
        self.refresh()

        def scan():
            cands = find_executables(g.prefix / "drive_c", g.name)
            GLib.idle_add(after, cands)

        def after(cands):
            self.busy.discard(slug)
            self.refresh()
            self._choose_exe_dialog(g, cands)
            return False
        threading.Thread(target=scan, daemon=True).start()

    def _choose_exe_dialog(self, g: library.Game, cands) -> None:
        dlg = Adw.AlertDialog(heading=f"Quel fichier lance « {g.name} » ?")
        dlg.set_body("Le plus probable est présélectionné." if cands
                     else "Aucun .exe évident trouvé. Choisis-le à la main.")
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        checks = []
        drive_c = g.prefix / "drive_c"
        for c in cands[:MAX_CANDIDATES]:
            check = Gtk.CheckButton(group=checks[0][0] if checks else None, valign=Gtk.Align.CENTER)
            try:
                where = "C:\\" + str(c.path.parent.relative_to(drive_c)).replace("/", "\\")
            except ValueError:
                where = str(c.path.parent)
            row = Adw.ActionRow(title=c.path.name, subtitle=f"{where} · {human_size(c.size)}",
                                use_markup=False, activatable_widget=check)
            row.add_prefix(check)
            listbox.append(row)
            checks.append((check, c.path))
        if checks:
            checks[0][0].set_active(True)
            scroller = Gtk.ScrolledWindow(child=listbox, min_content_height=min(70 * len(checks), 360),
                                          hscrollbar_policy=Gtk.PolicyType.NEVER)
            dlg.set_extra_child(scroller)
        dlg.add_response("later", "Plus tard")
        dlg.add_response("other", "Autre fichier…")
        if checks:
            dlg.add_response("ok", "Valider")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("ok")
        dlg.set_close_response("later")

        def on_response(_d, resp):
            if resp == "ok":
                for check, path in checks:
                    if check.get_active():
                        self.set_exe(g, path)
            elif resp == "other":
                self._pick_exe_manually(g)
        dlg.connect("response", on_response)
        dlg.present(self)

    def _pick_exe_manually(self, g: library.Game) -> None:
        dlg = Gtk.FileDialog(title=f"Exécutable de {g.name}")
        start = g.prefix / "drive_c" / "Games"
        dlg.set_initial_folder(Gio.File.new_for_path(str(start if start.is_dir() else g.prefix / "drive_c")))
        f = Gtk.FileFilter(name="Programmes Windows (*.exe)")
        f.add_suffix("exe")
        f.add_suffix("EXE")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except GLib.Error:
                return
            if file and file.get_path():
                self.set_exe(g, Path(file.get_path()))
        dlg.open(self, None, done)

    def set_exe(self, g: library.Game, path: Path) -> None:
        try:
            g.exe = str(path.relative_to(g.dir))
        except ValueError:
            g.exe = str(path)  # hors du dossier du jeu : chemin absolu
        g.status = library.READY
        g.save()
        desktop.extract_icon(path, g.icon)
        desktop.write_entry(g)
        self.refresh()
        self.toast(f"{g.name} est prêt : bouton « Jouer », ou cherche-le dans le menu K.")
        if g.slug in self.fresh:
            self.fresh.discard(g.slug)
            if g.delete_installer:
                self._delete_repack(g.slug, after_install=True)

    # ------------------------------------------------------- installateur

    def _delete_repack(self, slug: str, after_install: bool = False) -> None:
        g = library.get(slug)
        if not g:
            return
        if slug in self.installing:
            self.toast("L'installation est en cours : l'installateur est encore utilisé.")
            return
        paths = repack.targets(g.installer) if g.installer else []
        if not paths:
            if not after_install:
                self.toast("Installateur introuvable : déjà supprimé ou déplacé.")
            return
        total = repack.size(paths)
        what = (f"Le dossier :\n{paths[0]}" if len(paths) == 1 and paths[0].is_dir()
                else "Les fichiers :\n" + "\n".join(p.name for p in paths[:6])
                + ("\n…" if len(paths) > 6 else "") + f"\n(dans {paths[0].parent})")
        dlg = Adw.AlertDialog(
            heading="Supprimer l'installateur ?",
            body=(("Le jeu est installé, l'installateur ne sert plus.\n\n" if after_install else "")
                  + f"{what}\n\n{human_size(total)} libérés. Suppression définitive "
                  "(pas de corbeille) ; il faudra le retélécharger pour réinstaller."),
        )
        dlg.add_response("keep", "Garder")
        dlg.add_response("delete", "Supprimer")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("keep")

        def on_response(_d, resp):
            if resp != "delete":
                return

            def work():
                try:
                    repack.delete(paths)
                    err = None
                except OSError as e:
                    err = e
                GLib.idle_add(after, err)

            def after(err):
                self.toast(f"Suppression incomplète : {err}" if err
                           else f"Installateur supprimé : {human_size(total)} libérés.")
                return False
            threading.Thread(target=work, daemon=True).start()
        dlg.connect("response", on_response)
        if after_install:  # déjà demandé dans la fenêtre « Installer un jeu »
            on_response(dlg, "delete")
        else:
            dlg.present(self)

    # ---------------------------------------------------------- actions jeu

    def _play(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        if proton.is_running(g):
            self.toast(f"{g.name} est déjà lancé.")
            return
        try:
            proton.game_command(g)  # vérifie l'exe avant de partir dans un fil
        except (OSError, RuntimeError) as e:
            self.toast(str(e))
            return

        def work():
            err = None
            try:
                proton.play(g)
            except (OSError, RuntimeError) as e:
                err = e
            GLib.idle_add(done, err)

        def done(err):
            self.running.discard(slug)
            self.refresh()
            if err:
                self.toast(f"{g.name} : {err}")
            return False
        threading.Thread(target=work, daemon=True).start()
        self.running.add(slug)
        self.toast(f"Lancement de {g.name}…")
        self.refresh()

    def _poll_running(self):
        """Repère aussi les jeux lancés depuis le menu K ou Steam (fichier running.pid)."""
        now = {g.slug for g in library.all_games() if g.status == library.READY and proton.is_running(g)}
        if now != self.running:
            self.running = now
            self.refresh()
        return True

    def _poll_progress(self):
        """Taille écrite par l'installateur, mesurée en arrière-plan toutes les 2 s."""
        for slug in list(self.installing):
            g = library.get(slug)
            if not g:
                continue

            def measure(g=g, slug=slug):
                n = repack.size([g.prefix / "drive_c"]) if (g.prefix / "drive_c").is_dir() else 0
                GLib.idle_add(self._show_progress, slug, n)
            threading.Thread(target=measure, daemon=True).start()
        return True

    def _show_progress(self, slug, n):
        entry = self.progress.get(slug)
        if not entry or slug not in self.installing:
            return False
        bar, label, prev, t0 = entry
        now = time.time()
        speed = (n - prev) / max(now - t0, 0.1) if prev else 0
        self.progress[slug] = (bar, label, n, now)
        self._check_stall(slug, n, now)
        if bar:
            bar.pulse()
        if label and n:
            text = f"{human_size(n)} écrits"
            if speed > 1_000_000:
                text += f" · {human_size(speed)}/s"
            label.set_label(text)
        return False

    def _check_stall(self, slug, n, now):
        """Installation cachée qui n'écrit plus rien depuis STALL_SECONDS : on l'arrête.

        Sans fenêtre, un installateur figé (erreur de décompression, question inattendue)
        resterait « en cours » pour toujours. L'arrêt mène à « Réessayer avec la fenêtre ».
        """
        size, since = self.stall.get(slug, (0, now))
        if n > size + 1_000_000:
            self.stall[slug] = (n, now)
            return
        g = library.get(slug)
        if not g or g.install_mode != "auto" or now - since < STALL_SECONDS:
            return
        if not proton.hidden_wrapper(True):
            return  # fenêtres visibles : l'utilisateur voit lui-même ce qui se passe
        self.stall[slug] = (n, float("inf"))  # une seule fois
        with open(g.install_log, "a") as log:
            log.write(f"=== Rien d'écrit depuis {STALL_SECONDS // 60} min : installateur figé, arrêté\n")
        threading.Thread(target=proton.kill_prefix, args=(g,), daemon=True).start()
        self.toast(f"{g.name} : l'installation automatique est figée. Réessaie avec la fenêtre.")

    def _stop(self, slug: str) -> None:
        g = library.get(slug)
        if g:
            threading.Thread(target=proton.kill_prefix, args=(g,), daemon=True).start()
            self.toast(f"Arrêt forcé de {g.name}.")

    def _open_uri(self, path: Path) -> None:
        if not path.exists():
            self.toast(f"{path.name} n'existe pas encore.")
            return
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path)).get_uri(), None)

    def _open_dir(self, slug):
        g = library.get(slug)
        if g:
            games = g.prefix / "drive_c" / "Games"
            self._open_uri(games if games.is_dir() else g.dir)

    def _install_log(self, slug):
        g = library.get(slug)
        if g:
            self._open_uri(g.install_log)

    def _run_log(self, slug):
        g = library.get(slug)
        if g:
            self._open_uri(g.run_log)

    # ------------------------------------------------------------- réglages

    def _settings(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        dlg = Adw.PreferencesDialog(title=f"Réglages — {g.name}")
        page = Adw.PreferencesPage()
        dlg.add(page)

        perf = Adw.PreferencesGroup(title="Affichage et performances")
        page.add(perf)
        mango = Adw.SwitchRow(title="MangoHud", subtitle="Images par seconde, températures, charge",
                              active=g.mangohud)
        perf.add(mango)
        gs = Adw.SwitchRow(title="Gamescope",
                           subtitle="Session dédiée : résolution forcée, mise à l'échelle, VRR",
                           active=g.gamescope)
        perf.add(gs)
        gs_args = Adw.EntryRow(title="Options de Gamescope", text=g.gamescope_args or "")
        gs_args.set_tooltip_text("Exemple : -f -W 2560 -H 1440 -r 240")
        gs.bind_property("active", gs_args, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        perf.add(gs_args)

        comp = Adw.PreferencesGroup(title="Compatibilité")
        page.add(comp)
        protons = config.list_protons()
        default_name = Path(config.find_proton()).name
        names = [f"Par défaut ({default_name})"] + [p.name for p in protons]
        combo = Adw.ComboRow(title="Version de Proton", model=Gtk.StringList.new(names))
        current = next((i + 1 for i, p in enumerate(protons) if str(p) == g.proton), 0)
        combo.set_selected(current)
        comp.add(combo)
        args = Adw.EntryRow(title="Options de lancement du jeu", text=g.launch_args or "")
        args.set_tooltip_text("Passées au .exe, par exemple : -dx11 -skipintro")
        comp.add(args)
        env = Adw.EntryRow(title="Variables d'environnement", text=g.env or "")
        env.set_tooltip_text("Exemple : DXVK_HUD=fps PROTON_ENABLE_NVAPI=1")
        comp.add(env)

        def on_closed(*_):
            fresh = library.get(slug) or g
            fresh.mangohud = mango.get_active()
            fresh.gamescope = gs.get_active()
            fresh.gamescope_args = gs_args.get_text().strip()
            i = combo.get_selected()
            fresh.proton = str(protons[i - 1]) if i > 0 else ""
            fresh.launch_args = args.get_text().strip()
            fresh.env = env.get_text().strip()
            fresh.save()
            self.toast(f"Réglages de {fresh.name} enregistrés (pris en compte au prochain lancement).")
        dlg.connect("closed", on_closed)
        dlg.present(self)

    # --------------------------------------------------------------- Steam

    def _with_steam_closed(self, why: str, work, done) -> None:
        """Steam écrase shortcuts.vdf à sa fermeture : il doit être fermé pendant l'écriture."""
        def run(restart):
            def bg():
                err = None
                try:
                    if restart and not steam.shutdown():
                        raise RuntimeError("Steam ne s'est pas fermé à temps.")
                    result = work()
                except (OSError, RuntimeError, ValueError) as e:
                    err, result = e, None
                if restart:
                    steam.start()
                GLib.idle_add(lambda: (done(result, err), False)[1])
            threading.Thread(target=bg, daemon=True).start()

        if not steam.is_running():
            run(False)
            return
        dlg = Adw.AlertDialog(heading="Steam va redémarrer",
                              body=f"{why}\n\nSteam doit être fermé pendant la modification ; "
                                   "il sera fermé puis relancé automatiquement.")
        dlg.add_response("cancel", "Annuler")
        dlg.add_response("go", "Redémarrer Steam")
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: r == "go" and run(True))
        dlg.present(self)

    def _steam_toggle(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        if g.status != library.READY:
            self.toast("Choisis d'abord le .exe du jeu.")
            return
        if g.steam_shortcut:
            def work():
                return steam.remove(g.steam_shortcut)

            def done(_res, err):
                if err:
                    self.toast(f"Steam : {err}")
                    return
                fresh = library.get(slug) or g
                fresh.steam_shortcut = 0
                fresh.save()
                self.refresh()
                self.toast(f"{g.name} retiré de Steam.")
            self._with_steam_closed(f"Retirer « {g.name} » de ta bibliothèque Steam.", work, done)
            return

        def work():
            return steam.add(
                g.name, f'"{desktop.launcher_command()}"', f'"{g.dir}"',
                icon=str(g.icon) if g.icon.exists() else "",
                cover=g.cover if g.cover.exists() else None,
                launch_options=f"--play {g.slug}",
            )

        def done(appid, err):
            if err:
                self.toast(f"Steam : {err}")
                return
            fresh = library.get(slug) or g
            fresh.steam_shortcut = appid
            fresh.save()
            self.refresh()
            self.toast(f"{g.name} ajouté à Steam (jeux non-Steam), jaquette comprise.")
        self._with_steam_closed(f"Ajouter « {g.name} » à ta bibliothèque Steam, "
                                "pour le lancer depuis Big Picture à la manette.", work, done)

    # ---------------------------------------------------------- sauvegardes

    def _backup(self, slug: str, then=None) -> None:
        g = library.get(slug)
        if not g:
            return
        self.busy.add(slug)
        self.refresh()

        def work():
            err, arc = None, None
            try:
                arc = saves.backup(g)
            except OSError as e:
                err = e
            GLib.idle_add(done, arc, err)

        def done(arc, err):
            self.busy.discard(slug)
            self.refresh()
            if err:
                self.toast(f"Sauvegarde impossible : {err}")
            elif arc:
                self.toast(f"Saves de {g.name} sauvegardées ({human_size(arc.stat().st_size)}).")
            else:
                self.toast(f"{g.name} : rien à sauvegarder.")
            if then:
                then(err is None)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _restore(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        found = saves.backups(slug)
        if not found:
            self.toast(f"Aucune sauvegarde pour {g.name} dans {saves.backup_dir()}.")
            return
        dlg = Adw.AlertDialog(heading=f"Restaurer les saves de « {g.name} » ?",
                              body="Les fichiers de la sauvegarde remplacent ceux du jeu. "
                                   "Ferme le jeu avant.")
        names = Gtk.StringList.new(
            [time.strftime("%d/%m/%Y à %H:%M", time.strptime(p.name[len(slug) + 1:-7], "%Y%m%d-%H%M%S"))
             + f"  ({human_size(p.stat().st_size)})" for p in found])
        drop = Gtk.DropDown(model=names)
        dlg.set_extra_child(drop)
        dlg.add_response("cancel", "Annuler")
        dlg.add_response("restore", "Restaurer")
        dlg.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("cancel")

        def on_response(_d, resp):
            if resp != "restore":
                return
            arc = found[drop.get_selected()]
            try:
                saves.restore(g, arc)
            except (OSError, ValueError) as e:
                self.toast(f"Restauration impossible : {e}")
                return
            self.toast(f"Saves de {g.name} restaurées.")
        dlg.connect("response", on_response)
        dlg.present(self)

    # -------------------------------------------------------- désinstallation

    def _uninstall(self, slug: str) -> None:
        g = library.get(slug)
        if not g:
            return
        if slug in self.installing:
            self.toast("Attends la fin de l'installation avant de désinstaller.")
            return
        dlg = Adw.AlertDialog(
            heading=f"Désinstaller « {g.name} » ?",
            body=f"Tout le dossier {g.dir} sera supprimé : le jeu et son environnement Windows. "
                 "L'installateur d'origine n'est pas touché.",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        keep_saves = Gtk.CheckButton(label=f"Sauvegarder les saves avant (dans {saves.backup_dir().name})",
                                     active=True)
        box.append(keep_saves)
        drop_steam = None
        if g.steam_shortcut:
            drop_steam = Gtk.CheckButton(label="Retirer aussi de Steam", active=True)
            box.append(drop_steam)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Annuler")
        dlg.add_response("delete", "Désinstaller")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")

        def remove_files(ok=True):
            if not ok:
                self.toast("Désinstallation annulée : la sauvegarde des saves a échoué.")
                return
            self.busy.add(slug)
            self.refresh()

            def work():
                proton.kill_prefix(g)
                err = None
                try:
                    shutil.rmtree(g.dir)
                except OSError as e:
                    err = e
                GLib.idle_add(after, err)

            def after(err):
                self.busy.discard(slug)
                desktop.remove_entry(g)
                self.refresh()
                self.toast(f"Erreur pendant la suppression : {err}" if err else f"{g.name} désinstallé.")
                return False
            threading.Thread(target=work, daemon=True).start()

        def after_steam():
            if keep_saves.get_active():
                self._backup(slug, then=remove_files)
            else:
                remove_files()

        def on_response(_d, resp):
            if resp != "delete":
                return
            if drop_steam is not None and drop_steam.get_active():
                def done(_res, err):
                    if err:
                        self.toast(f"Steam : {err} (raccourci laissé en place)")
                    after_steam()
                self._with_steam_closed(f"Retirer « {g.name} » de Steam.",
                                        lambda: steam.remove(g.steam_shortcut), done)
            else:
                after_steam()
        dlg.connect("response", on_response)
        dlg.present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None

    def _window(self) -> Window:
        if not self.win:
            self.win = Window(self)
        self.win.present()
        return self.win

    def do_activate(self):
        self._window()

    def do_open(self, files, n_files, hint):
        win = self._window()
        for f in files:
            if f.get_path():
                win.start_install(Path(f.get_path()))


def run(argv) -> int:
    return App().run(argv)
