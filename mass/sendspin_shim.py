"""`sendspin` for the Flatpak: the same daemon, with system volume that can actually be set.

PipeWire gives sandboxed clients read-only access to other apps' devices, so inside the
Flatpak Sendspin can read and follow the sink volume but setting it fails
(PulseOperationFailed) and server volume commands silently do nothing. When that happens,
set it on the host with pactl instead. Reading and watching still go through PipeWire, so
volume keys keep syncing back to Music Assistant.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

log = logging.getLogger("mass.sendspin")


async def _host(*argv: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "flatpak-spawn", "--host", *argv,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
    if proc.returncode:
        raise RuntimeError(f"{' '.join(argv)} failed: {err.decode().strip()}")


def _patch() -> None:
    try:
        import pulsectl
        import pulsectl_asyncio
        from sendspin import hardware_volume as hv
    except ImportError:
        return
    orig = hv.HardwareVolumeController.set_state

    async def set_state(self, volume: int, *, muted: bool) -> None:
        try:
            await orig(self, volume, muted=muted)
            return
        except pulsectl.PulseOperationFailed:
            pass  # sandboxed: no write access to the sink
        async with pulsectl_asyncio.PulseAsync("sendspin-cli") as client:
            sink = await hv._get_sink(self._audio_device, client)
        if sink is None:
            raise RuntimeError("No PulseAudio sink available for hardware volume")
        await _host("pactl", "set-sink-volume", sink.name, f"{volume}%")
        await _host("pactl", "set-sink-mute", sink.name, "1" if muted else "0")

    hv.HardwareVolumeController.set_state = set_state


def main() -> int:
    if Path("/.flatpak-info").exists():
        _patch()
    from sendspin.cli import main as sendspin_main

    return sendspin_main()


if __name__ == "__main__":
    sys.exit(main())
