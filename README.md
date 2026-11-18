# Mass

A desktop player for [Music Assistant](https://music-assistant.io), built with GTK4 and libadwaita in the
Material 3 Expressive style.

- **Live theming**: follows your matugen or [DankMaterialShell](https://github.com/AvengeMedia/DankMaterialShell)
  palette as it changes, or themes itself from the current album art.
- **Every player**: browse, search and control all Music Assistant players, and transfer queues between them.
- **This PC as a speaker**: registers the computer as a Music Assistant player through Sendspin.
  It can run as a systemd user service, so music keeps playing after you close the window.
- **Lyrics**: time-synced; click a line to jump to it.
- **AI Radio**: AI DJ hosts per queue, live "on air" captions, and stations (needs the server's AI Radio plugin).
- **Desktop integration**: MPRIS, notifications with cover art, a tray icon (StatusNotifierItem),
  start on login, and media shortcuts.
- **MASS menu**: sleep timer, audio overlay, announcements, autoplay, crossfade and save queue as playlist,
  all matching the Music Assistant web UI.

Needs a Music Assistant server (schema 28+). You sign in with your MA account, and the token is stored in your keyring.

## Install

### Flatpak (auto-updating)

```sh
flatpak install --user https://dukko.github.io/mass/mass.flatpakref
```

This installs from a signed Flatpak repo on GitHub Pages that CI rebuilds on every push to `main`,
so `flatpak update` (or your software center) picks up new versions. Mass also updates itself: while
it's running it checks the repo every few hours, downloads new versions in the background, and asks
you to restart (banner + notification). Toggle this in Preferences → Desktop → Updates.

A single-file `mass.flatpak` bundle is also attached to each [release](https://github.com/Dukko/mass/releases)
(no automatic updates).

To build it yourself:

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51
flatpak run org.flatpak.Builder --user --install --force-clean build-dir flatpak/io.github.mass_matugen.Mass.yml
```

The Flatpak can run host commands (`flatpak-spawn --host`). It needs that to call your `matugen` for album-art
colors and to manage the `mass-sendspin` systemd user service. It shares `~/.config/mass` with a native install.

### Native (Arch / CachyOS)

```sh
sudo pacman -S --needed python-gobject gtk4 libadwaita libsecret
./install.sh
```

Optional: `matugen` (album-art colors), `ttf-material-symbols-variable` (icons).

## matugen

DankMaterialShell users don't need to set anything up. For plain matugen, add the template from
`data/matugen/config-snippet.toml` to `~/.config/matugen/config.toml`.

## Development

```sh
python -m venv --system-site-packages .venv && .venv/bin/pip install -e .
.venv/bin/python -m mass            # MASS_LOG=debug for verbose logs
tools/gen-python-deps.py            # after changing Python dependencies (regenerates the Flatpak deps)
```
