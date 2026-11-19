"""Local playback endpoint: makes this PC a Music Assistant player via Sendspin.

Preferred: the `mass-sendspin.service` systemd *user* unit (installed by install.sh),
so audio keeps playing when the window is closed and starts with your session.
Fallback: a child `sendspin daemon` process owned by the app.

Either way the daemon advertises itself over mDNS and MA connects to it.
Its own MPRIS is disabled; the app's MPRIS server already covers it.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
import sys
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from . import APP_ID
from .config import CONFIG_DIR
from .host import HOST_CONFIG, IN_FLATPAK, host_cmd

log = logging.getLogger(__name__)

UNIT = "mass-sendspin.service"
UNIT_TEMPLATE = """[Unit]
Description=Mass local Music Assistant player (Sendspin)
After=pipewire.service pipewire-pulse.service network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={env}
ExecStart=/bin/sh -c 'eval exec {cmd} $$SENDSPIN_ARGS'
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
ENV_FILE = CONFIG_DIR / "sendspin.env"
SETTINGS_DIR = CONFIG_DIR / "sendspin"


def _sendspin_bin() -> str | None:
    # prefer the one next to our interpreter (the venv), then PATH
    local = Path(sys.executable).with_name("sendspin")
    return str(local) if local.exists() else shutil.which("sendspin")


class Endpoint(GObject.Object):
    __gsignals__ = {"state-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.state = "stopped"  # stopped | running | error
        self.mode = "none"  # systemd | child | none
        self.detail = ""
        self._proc: Gio.Subprocess | None = None
        self._applying = False
        self._reapply = False

    def args(self) -> list[str]:
        c = self.config
        return [
            "daemon",
            "--name", c.endpoint_name,
            "--audio-device", c.endpoint_audio_device,
            "--hardware-volume", "true" if c.endpoint_hardware_volume else "false",
            "--settings-dir", str(SETTINGS_DIR),
            "--product-name", "Mass desktop",
            "--disable-mpris",
        ]

    def _write_env(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # double-quoted for systemd; the unit `eval`s it so shlex quoting survives
        val = shlex.join(self.args())
        for ch in ("\\", '"', "$", "`"):
            val = val.replace(ch, "\\" + ch)
        ENV_FILE.write_text(f'SENDSPIN_ARGS="{val}"\n')

    def _set(self, state: str, mode: str, detail: str = "") -> None:
        self.state, self.mode, self.detail = state, mode, detail
        self.emit("state-changed")

    @staticmethod
    def _systemctl(*args: str) -> tuple[bool, str]:
        try:
            p = Gio.Subprocess.new(
                host_cmd(["systemctl", "--user", *args]),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE,
            )
            _ok, out, _ = p.communicate_utf8(None, None)
            return p.get_successful(), (out or "").strip()
        except GLib.Error as err:
            return False, err.message

    def _install_flatpak_unit(self) -> None:
        """Flatpak: install a host user unit that runs sendspin from this Flatpak."""
        unit = HOST_CONFIG / "systemd" / "user" / UNIT
        text = UNIT_TEMPLATE.format(env=ENV_FILE, cmd=f"flatpak run --die-with-parent --command=mass-sendspin {APP_ID}")
        try:
            if unit.exists() and unit.read_text() == text:
                return
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(text)
        except OSError as err:
            log.warning("cannot install %s: %s", unit, err)
            return
        self._systemctl("daemon-reload")

    def has_unit(self) -> bool:
        ok, _ = self._systemctl("cat", UNIT)
        return ok

    async def apply(self) -> None:
        """Start, restart or stop the endpoint to match the config.

        The systemctl work runs in a thread; a call made while one is in flight
        is folded into a single re-run so the last config wins.
        """
        if self._applying:
            self._reapply = True
            return
        self._applying = True
        try:
            while True:
                self._reapply = False
                await self._apply_once()
                if not self._reapply:
                    return
        finally:
            self._applying = False

    async def _apply_once(self) -> None:
        if not self.config.endpoint_enabled:
            await self.stop()
            return
        try:
            has_unit = await asyncio.to_thread(self._prepare_unit)
        except OSError as err:
            self._set("error", "none", str(err))
            return
        if not has_unit:
            self._start_child()
            return
        self._stop_child()
        ok, out = await asyncio.to_thread(self._restart_unit)
        self._set("running" if ok else "error", "systemd", "" if ok else out)

    def _prepare_unit(self) -> bool:
        self._write_env()
        if IN_FLATPAK:
            self._install_flatpak_unit()
        return self.has_unit()

    def _restart_unit(self) -> tuple[bool, str]:
        self._systemctl("enable", UNIT)  # so it also comes up at login
        return self._systemctl("restart", UNIT)

    async def stop(self) -> None:
        if await asyncio.to_thread(self.has_unit):
            await asyncio.to_thread(self._systemctl, "disable", "--now", UNIT)
        self._stop_child()
        self._set("stopped", "none")

    def shutdown(self) -> None:
        """App exit: only the child process dies with us; the service keeps running."""
        self._stop_child()

    def _start_child(self) -> None:
        self._stop_child()
        exe = _sendspin_bin()
        if not exe:
            self._set("error", "child", "sendspin is not installed")
            return
        try:
            # --pdeathsig: the kernel kills sendspin if the app dies without cleaning up
            prefix = ["setpriv", "--pdeathsig", "TERM"] if shutil.which("setpriv") else []
            self._proc = Gio.Subprocess.new([*prefix, exe, *self.args()], Gio.SubprocessFlags.NONE)
        except GLib.Error as err:
            self._set("error", "child", err.message)
            return
        proc = self._proc

        def exited(p, res):
            try:
                p.wait_finish(res)
            except GLib.Error:
                pass
            if self._proc is proc:
                self._proc = None
                self._set("error", "child", f"sendspin exited ({p.get_exit_status()})")

        proc.wait_async(None, exited)
        self._set("running", "child")

    def _stop_child(self) -> None:
        if self._proc:
            proc, self._proc = self._proc, None
            proc.send_signal(15)

    def find_player_id(self, backend) -> str | None:
        """The MA player id that belongs to this endpoint (matched by name + provider)."""
        for p in backend.players():
            if p.name == self.config.endpoint_name and "sendspin" in (p.provider or "").lower():
                return p.player_id
        for p in backend.players():
            if p.name == self.config.endpoint_name:
                return p.player_id
        return None
