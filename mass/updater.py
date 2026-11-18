"""In-app updates for the Flatpak build.

Compares three commits:
  running   – what this process was started from (/.flatpak-info)
  installed – what `flatpak info` has deployed
  remote    – the newest commit on the origin remote (the signed GitHub Pages repo)

remote ≠ installed → an update is available (downloaded automatically when auto_update is on)
installed ≠ running → an update is installed; restarting the app applies it

Everything runs on the host via flatpak-spawn (the app already talks to org.freedesktop.Flatpak).
Native (non-Flatpak) runs have nothing to update and keep the updater disabled.
"""

from __future__ import annotations

import asyncio
import configparser
import logging
import time

from gi.repository import Gio, GLib, GObject

from . import APP_ID
from .host import IN_FLATPAK, host_cmd

log = logging.getLogger(__name__)

FIRST_CHECK = 90  # seconds after startup
INTERVAL = 3 * 3600  # then every 3 hours while running


def _running_commit() -> str:
    cp = configparser.ConfigParser()
    try:
        cp.read("/.flatpak-info")
        return cp.get("Instance", "app-commit", fallback="")
    except configparser.Error:
        return ""


def _run(argv: list[str]) -> tuple[bool, str]:
    """Blocking host command; call through asyncio.to_thread."""
    try:
        p = Gio.Subprocess.new(host_cmd(argv), Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        _ok, out, _ = p.communicate_utf8(None, None)
        return p.get_successful(), (out or "").strip()
    except GLib.Error as err:
        return False, err.message


class Updater(GObject.Object):
    """state: disabled | idle | checking | up-to-date | available | downloading | ready | error"""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.running = _running_commit() if IN_FLATPAK else ""
        self.state = "idle" if self.running else "disabled"
        self.detail = ""
        self.installed = self.remote = ""
        self.last_check = 0.0
        self._scope = "--user"
        self._busy = False

    @property
    def enabled(self) -> bool:
        return self.state != "disabled"

    def _set(self, state: str, detail: str = "") -> None:
        self.state, self.detail = state, detail
        self.emit("changed")

    def start(self) -> None:
        if not self.enabled:
            return
        GLib.timeout_add_seconds(FIRST_CHECK, lambda: (self.check(), False)[1])
        GLib.timeout_add_seconds(INTERVAL, lambda: (self.check(), True)[1])

    # ------------------------------------------------------------------ check / install

    def check(self) -> None:
        if self.enabled and not self._busy:
            asyncio.ensure_future(self._check())

    async def _check(self) -> None:
        self._busy = True
        try:
            if self.state not in ("downloading", "ready"):
                self._set("checking")
            ok, installed = await asyncio.to_thread(_run, ["flatpak", "info", "--user", "-c", APP_ID])
            if not ok:
                self._scope = "--system"
                ok, installed = await asyncio.to_thread(_run, ["flatpak", "info", "--system", "-c", APP_ID])
            _, origin = await asyncio.to_thread(_run, ["flatpak", "info", self._scope, "-o", APP_ID])
            ok_r, remote = await asyncio.to_thread(
                _run, ["flatpak", "remote-info", self._scope, origin or "mass", APP_ID, "-c"])
            self.last_check = time.time()
            self.installed = installed if ok else ""
            if ok_r and remote and remote != self.installed:
                self.remote = remote
                self._set("available")
                if self.config.auto_update:
                    await self._install()
                return
            if not ok_r:
                log.info("update check failed: %s", remote)
            if self.installed and self.installed != self.running:
                self._set("ready")
            elif not ok_r:
                self._set("error", "Couldn’t reach the update server")
            else:
                self._set("up-to-date")
        finally:
            self._busy = False

    def install(self) -> None:
        if self.enabled and not self._busy:
            asyncio.ensure_future(self._install_now())

    async def _install_now(self) -> None:
        self._busy = True
        try:
            await self._install()
        finally:
            self._busy = False

    async def _install(self) -> None:
        self._set("downloading")
        ok, out = await asyncio.to_thread(
            _run, ["flatpak", "update", self._scope, "--noninteractive", "-y", APP_ID])
        if not ok:
            log.warning("flatpak update failed: %s", out)
            self._set("error", out.splitlines()[-1] if out else "Update failed")
            return
        _, self.installed = await asyncio.to_thread(_run, ["flatpak", "info", self._scope, "-c", APP_ID])
        self._set("ready" if self.installed != self.running else "up-to-date")

    # ------------------------------------------------------------------ restart

    def restart(self, app, show_window: bool) -> None:
        """Relaunch the updated app on the host once this instance has left the bus, then quit.

        systemd-run detaches the waiter from our sandbox so it survives our exit.
        """
        launch = f"flatpak run {APP_ID}" + ("" if show_window else " --background")
        script = (f"for i in $(seq 100); do busctl --user status {APP_ID} >/dev/null 2>&1 || break; "
                  f"sleep 0.2; done; exec {launch}")
        ok, out = _run(["systemd-run", "--user", "--collect", "--quiet",
                        f"--unit=mass-relaunch-{int(time.time())}", "sh", "-c", script])
        if not ok:
            log.warning("cannot schedule relaunch: %s", out)
            self._set("error", "Couldn’t restart automatically — quit and reopen Mass")
            return
        app.quit()

    # ------------------------------------------------------------------ text

    def describe(self) -> str:
        short = lambda c: c[:7] if c else "?"  # noqa: E731
        return {
            "disabled": "Updates are managed by your package manager",
            "idle": f"Version {short(self.running)}",
            "checking": "Checking for updates…",
            "up-to-date": f"Up to date ({short(self.running)})",
            "available": f"Update available ({short(self.running)} → {short(self.remote)})",
            "downloading": "Downloading update…",
            "ready": f"Update installed ({short(self.installed)}) — restart to apply",
            "error": self.detail or "Update failed",
        }.get(self.state, self.state)
