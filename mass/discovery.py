"""mDNS discovery of Music Assistant servers (_mass._tcp)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

SERVICE = "_mass._tcp.local."


@dataclass(frozen=True)
class DiscoveredServer:
    name: str
    url: str
    version: str


async def discover(timeout: float = 3.0) -> list[DiscoveredServer]:
    found: dict[str, DiscoveredServer] = {}
    pending: set[asyncio.Task] = set()
    azc = AsyncZeroconf()

    async def resolve(name: str) -> None:
        info = AsyncServiceInfo(SERVICE, name)
        if not await info.async_request(azc.zeroconf, 2000):
            return
        props = {
            k.decode(): (v.decode() if isinstance(v, bytes) else "")
            for k, v in info.properties.items()
        }
        addrs = info.parsed_scoped_addresses()
        v4 = [a for a in addrs if ":" not in a]
        host = (v4 or addrs or [None])[0]
        if not host:
            return
        base = props.get("base_url") or f"http://{host}"
        # base_url omits the port when it's served through the default webserver port
        url = base if base.count(":") >= 2 else f"{base}:{info.port}"
        found[props.get("server_id", name)] = DiscoveredServer(
            name=props.get("name", name.split(".")[0]),
            url=url,
            version=props.get("server_version", "?"),
        )

    def on_change(zeroconf, service_type, name, state_change):  # noqa: ARG001
        if state_change is ServiceStateChange.Added:
            task = asyncio.ensure_future(resolve(name))
            pending.add(task)
            task.add_done_callback(pending.discard)

    browser = AsyncServiceBrowser(azc.zeroconf, [SERVICE], handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
        if pending:
            await asyncio.wait(pending, timeout=2)
    finally:
        await browser.async_cancel()
        await azc.async_close()
    return list(found.values())
