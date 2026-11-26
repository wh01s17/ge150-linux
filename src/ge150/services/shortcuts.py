"""Shortcuts to open the editor, made to fit the user's desktop (pipx/pip installs bring none).

* Always: the freedesktop menu entry (``$XDG_DATA_HOME/applications/ge150.desktop``) and its
  icon. Every desktop's application menu reads it (GNOME, KDE, Xfce, Cinnamon, MATE, LXQt,
  Budgie…), and so do the launchers of tiling window managers (rofi, wofi, fuzzel… under
  Hyprland, Sway, i3).
* Also an icon on the desktop, only where the desktop shows icons (KDE, Xfce, Cinnamon, MATE,
  LXQt, LXDE, Budgie, Deepin) and never when the desktop folder is the home folder. It is
  made executable and marked as trusted, so it opens without a warning.

A system-wide entry (the Arch package's) is not duplicated. Nothing here needs root.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ge150.i18n import tr

APP_ID = "ge150"
NAME = "GE150 Editor"

#: XDG_CURRENT_DESKTOP (lower case) -> name shown to the user.
DESKTOPS = {
    "kde": "KDE Plasma", "gnome": "GNOME", "xfce": "Xfce", "x-cinnamon": "Cinnamon",
    "cinnamon": "Cinnamon", "mate": "MATE", "lxqt": "LXQt", "lxde": "LXDE", "budgie": "Budgie",
    "budgie-desktop": "Budgie", "unity": "Unity", "pantheon": "Pantheon", "deepin": "Deepin",
    "cosmic": "COSMIC", "hyprland": "Hyprland", "sway": "Sway", "i3": "i3", "niri": "niri",
    "river": "river", "qtile": "Qtile", "awesome": "awesome", "bspwm": "bspwm",
    "openbox": "Openbox", "wayfire": "Wayfire", "labwc": "labwc",
}
#: Desktops whose desktop shows icons (GNOME needs an extension for that).
DESKTOP_ICONS = {"kde", "xfce", "x-cinnamon", "cinnamon", "mate", "lxqt", "lxde", "budgie",
                 "budgie-desktop", "deepin"}
#: Window managers without a desktop: an application launcher reads the menu entry.
TILING = {"hyprland", "sway", "i3", "niri", "river", "qtile", "awesome", "bspwm", "wayfire"}


@dataclass(frozen=True)
class Environment:
    desktop: str  # lower-case XDG_CURRENT_DESKTOP token, "" if unknown

    @property
    def name(self) -> str:
        return DESKTOPS.get(self.desktop, self.desktop.capitalize() or "tu escritorio")

    @property
    def tiling(self) -> bool:
        return self.desktop in TILING

    @property
    def desktop_icons(self) -> bool:
        return self.desktop in DESKTOP_ICONS


def detect(env: dict[str, str] | None = None) -> Environment:
    env = dict(os.environ) if env is None else env
    for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        for token in env.get(key, "").lower().split(":"):
            if token in DESKTOPS:
                return Environment(token)
    for key, desktop in (("HYPRLAND_INSTANCE_SIGNATURE", "hyprland"), ("SWAYSOCK", "sway"),
                         ("I3SOCK", "i3"), ("KDE_FULL_SESSION", "kde"),
                         ("GNOME_DESKTOP_SESSION_ID", "gnome")):
        if env.get(key):
            return Environment(desktop)
    first = env.get("XDG_CURRENT_DESKTOP", "").lower().split(":")[0]
    return Environment(first)


def _home(env: dict[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def data_home(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    return Path(env.get("XDG_DATA_HOME") or _home(env) / ".local" / "share")


def desktop_dir(env: dict[str, str] | None = None) -> Path | None:
    """The desktop folder (``xdg-user-dirs``), or None if there is none or it is the home."""
    env = dict(os.environ) if env is None else env
    home = _home(env)
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    folder: Path | None = None
    try:
        text = (config / "user-dirs.dirs").read_text()
    except OSError:
        text = ""
    match = re.search(r'^XDG_DESKTOP_DIR="?([^"\n]*)"?', text, re.MULTILINE)
    if match:
        folder = Path(match.group(1).replace("$HOME", str(home)))
    elif (home / "Desktop").is_dir():
        folder = home / "Desktop"
    if folder is None or not folder.is_dir() or folder.resolve() == home.resolve():
        return None
    return folder


def system_entry(env: dict[str, str] | None = None) -> Path | None:
    """A menu entry installed for everyone (e.g. by the Arch package)."""
    env = dict(os.environ) if env is None else env
    for base in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        path = Path(base) / "applications" / f"{APP_ID}.desktop"
        if base and path.is_file():
            return path
    return None


def command() -> str:
    """How to start the editor: the absolute path of ``ge150`` (``~/.local/bin/ge150`` with
    pipx), so the entry works even if ``~/.local/bin`` is not in the session's PATH."""
    found = shutil.which(APP_ID)
    if found:
        return shlex.quote(str(Path(found).absolute()))
    argv0 = Path(sys.argv[0])
    if argv0.name == APP_ID and argv0.exists():
        return shlex.quote(str(argv0.absolute()))
    return f"{shlex.quote(sys.executable)} -m ge150"


def entry_text(exec_line: str, icon: str = APP_ID) -> str:
    return "\n".join([
        "[Desktop Entry]", "Type=Application", f"Name={NAME}",
        "GenericName=Multi-effects pedal editor",
        "Comment=Edit presets, IRs and backups of the Mooer GE150",
        "Comment[es]=Edita presets, IR y backups de la Mooer GE150",
        f"Exec={exec_line}", f"Icon={icon}", "Terminal=false",
        "Categories=AudioVideo;Audio;Music;", "Keywords=guitar;pedal;mooer;ge150;effects;preset;",
        f"StartupWMClass={APP_ID}", "",
    ])


@dataclass(frozen=True)
class Plan:
    environment: Environment
    menu: Path | None  # None: a system-wide entry already exists
    icon: Path
    desktop: Path | None  # None: this desktop shows no icons

    @property
    def paths(self) -> list[Path]:
        return [p for p in (self.menu, self.icon, self.desktop) if p is not None]

    def describe(self) -> str:
        """For the question at first start: where the editor will show up."""
        env = self.environment
        where = []
        if self.menu is not None:
            menu = ("el lanzador de aplicaciones de {desktop} (rofi, wofi, fuzzel…)"
                    if env.tiling else "el menú de aplicaciones de {desktop}")
            where.append(tr(menu, desktop=env.name))
        if self.desktop is not None:
            where.append(tr("el escritorio"))
        return tr(" y en ").join(where)


def plan(env: dict[str, str] | None = None) -> Plan:
    env = dict(os.environ) if env is None else env
    environment = detect(env)
    data = data_home(env)
    menu = None if system_entry(env) else data / "applications" / f"{APP_ID}.desktop"
    icon = data / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    folder = desktop_dir(env) if environment.desktop_icons else None
    return Plan(environment, menu, icon, folder / f"{APP_ID}.desktop" if folder else None)


def installed(env: dict[str, str] | None = None) -> bool:
    """The editor already has a menu entry (the user's or the system's)."""
    target = plan(env)
    return target.menu is None or target.menu.is_file()


def install(env: dict[str, str] | None = None) -> list[Path]:
    """Create the shortcuts of :func:`plan`; returns what was written."""
    target = plan(env)
    target.icon.parent.mkdir(parents=True, exist_ok=True)
    target.icon.write_bytes(resources.files("ge150.data").joinpath("ge150.svg").read_bytes())
    text = entry_text(command())
    written = [target.icon]
    for path in (target.menu, target.desktop):
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)  # desktop icons only open when executable
        written.append(path)
    if target.desktop is not None:
        _trust(target.desktop, text)
    _refresh(data_home(env), target.environment)
    return written


def remove(env: dict[str, str] | None = None) -> list[Path]:
    """Delete what :func:`install` made (never a system-wide entry); returns what was."""
    target = plan(env)
    removed = []
    for path in target.paths:
        if path.is_file():
            path.unlink()
            removed.append(path)
    _refresh(data_home(env), target.environment)
    return removed


def _run(*args: str) -> None:
    if shutil.which(args[0]):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       timeout=20)


def _trust(path: Path, text: str) -> None:
    """Nautilus/Nemo/Caja want ``metadata::trusted``; Xfce, the file's checksum."""
    _run("gio", "set", str(path), "metadata::trusted", "true")
    checksum = hashlib.sha256(text.encode()).hexdigest()
    _run("gio", "set", str(path), "metadata::xfce-exe-checksum", checksum)


def _refresh(data: Path, environment: Environment) -> None:
    """Let menus notice now (each is optional: menus also rescan on their own)."""
    _run("update-desktop-database", str(data / "applications"))
    _run("gtk-update-icon-cache", "-f", "-t", str(data / "icons" / "hicolor"))
    if environment.desktop == "kde":
        _run("kbuildsycoca6")
