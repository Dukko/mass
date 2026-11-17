#!/usr/bin/env python3
"""Regenerate flatpak/python3-deps.json (pinned PyPI wheels/sdists for the Flatpak build).

Resolves inside the GNOME SDK so wheels match its Python/glibc exactly:
    tools/gen-python-deps.py            # uses org.gnome.Sdk//$RUNTIME (default 51)
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


def pypi_file(filename: str) -> dict:
    if filename.endswith(".whl"):
        name, version = filename.split("-")[:2]
    else:  # sdist: <name-with-dashes>-<version>.tar.gz
        name, version = filename.removesuffix(".tar.gz").removesuffix(".zip").rsplit("-", 1)
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as r:
        data = json.load(r)
    for u in data["urls"]:
        if u["filename"] == filename:
            return {"type": "file", "url": u["url"], "sha256": u["digests"]["sha256"]}
    sys.exit(f"{filename}: not found on PyPI")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["flatpak", "run", "--user", "--share=network", f"--filesystem={tmp}", "--command=python3",
             f"org.gnome.Sdk//{RUNTIME}", "-m", "pip", "download", "-q", "--prefer-binary", "-d", tmp, *REQS],
            check=True)
        files = sorted(f for f in os.listdir(tmp) if f.split("-")[0].lower().replace("_", "-") not in SKIP)
    sources = [pypi_file(f) for f in files]
    module = {
        "name": "python3-deps",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --verbose --exists-action=i --no-index --find-links=\"file://${PWD}\" "
            "--prefix=${FLATPAK_DEST} --no-build-isolation " + " ".join(f'"{r}"' for r in REQS)
        ],
        "sources": sources,
    }
    out = ROOT / "flatpak" / "python3-deps.json"
    out.write_text(json.dumps(module, indent=2) + "\n")
    print(f"wrote {out} ({len(sources)} files)")


if __name__ == "__main__":
    main()
