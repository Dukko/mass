#!/usr/bin/env python3
"""Regenerate flatpak/python3-deps.json (pinned PyPI wheels/sdists for the Flatpak build).

Resolves inside the GNOME SDK so wheels match its Python/glibc exactly:
    tools/gen-python-deps.py            # uses org.gnome.Sdk//$RUNTIME (default 51)

Every compiled x86_64 wheel is paired with the same-version aarch64 wheel; Flatpak
skips the other architecture with only-arches. If a package has no aarch64 wheel,
its sdist is used instead so both architectures build it.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = os.environ.get("RUNTIME", "51")
REQS = ["music-assistant-client>=1.8", "sendspin>=9.0", "aiohttp>=3.9", "zeroconf>=0.130"]
# provided by the GNOME runtime
SKIP = {"pygobject", "pycairo", "setuptools", "pip", "wheel"}

_FILES: dict[tuple[str, str], dict[str, dict]] = {}


def release_files(name: str, version: str) -> dict[str, dict]:
    key = (name, version)
    if key not in _FILES:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as r:
            _FILES[key] = {u["filename"]: u for u in json.load(r)["urls"]}
    return _FILES[key]


def split_filename(filename: str) -> tuple[str, str]:
    if filename.endswith(".whl"):
        name, version = filename[:-4].split("-")[:2]
    else:  # sdist: <name-with-dashes>-<version>.tar.gz
        base = filename.removesuffix(".tar.gz").removesuffix(".zip")
        name, version = base.rsplit("-", 1)
    return name, version


def arch_wheel(filename: str, release: dict[str, dict], arch: str) -> str | None:
    """The same build of a wheel for `arch`, even when the manylinux tags differ.

    Prefers the exact platform tag, then manylinux (the GNOME runtime is glibc).
    """
    exact = filename.replace("x86_64", arch)
    if exact in release:
        return exact
    parts = filename[:-4].split("-")
    prefix, py, abi = "-".join(parts[:-3]), parts[-3], parts[-2]
    candidates = []
    for name in release:
        if not name.endswith(".whl"):
            continue
        cand = name[:-4].split("-")
        if (len(cand) >= 5 and "-".join(cand[:-3]) == prefix and cand[-3] == py
                and cand[-2] == abi and "linux" in cand[-1] and f"_{arch}" in cand[-1]):
            candidates.append(name)
    manylinux = [n for n in candidates if "manylinux" in n]
    return (manylinux or candidates or [None])[0]


def sdist(release: dict[str, dict]) -> str | None:
    return next((n for n in release if n.endswith((".tar.gz", ".zip"))), None)


def source(filename: str, arches: list[str] | None = None) -> dict:
    name, version = split_filename(filename)
    u = release_files(name, version)[filename]
    out = {"type": "file", "url": u["url"], "sha256": u["digests"]["sha256"]}
    if arches:
        out["only-arches"] = arches
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["flatpak", "run", "--user", "--share=network", f"--filesystem={tmp}", "--command=python3",
             f"org.gnome.Sdk//{RUNTIME}", "-m", "pip", "download", "-q", "--prefer-binary", "-d", tmp, *REQS],
            check=True)
        files = sorted(f for f in os.listdir(tmp) if f.split("-")[0].lower().replace("_", "-") not in SKIP)

    sources: list[dict] = []
    for filename in files:
        name, version = split_filename(filename)
        release = release_files(name, version)
        if filename.endswith(".whl") and "none-any" in filename:
            sources.append(source(filename))
        elif filename.endswith(".whl"):
            x86 = arch_wheel(filename, release, "x86_64")
            arm = arch_wheel(filename, release, "aarch64")
            if x86 and arm:
                sources.append(source(x86, ["x86_64"]))
                sources.append(source(arm, ["aarch64"]))
            elif fallback := sdist(release):
                sources.append(source(fallback))  # build from source on both arches
            else:
                sys.exit(f"{filename}: no aarch64 wheel or sdist")
        else:
            sources.append(source(filename))

    unique, seen = [], set()
    for s in sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)

    module = {
        "name": "python3-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --verbose --exists-action=i --no-index --find-links=\"file://${PWD}\" "
            "--prefix=${FLATPAK_DEST} --no-build-isolation " + " ".join(f'"{r}"' for r in REQS)
        ],
        "sources": unique,
    }
    out = ROOT / "flatpak" / "python3-deps.json"
    out.write_text(json.dumps(module, indent=2) + "\n")
    print(f"wrote {out} ({len(unique)} files)")


if __name__ == "__main__":
    main()
