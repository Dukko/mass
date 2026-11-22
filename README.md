<div align="center">

# Mass

**A desktop player for [Music Assistant](https://music-assistant.io), in Material 3 Expressive.**

GTK4 · libadwaita · themed live by matugen / [DankMaterialShell](https://github.com/AvengeMedia/DankMaterialShell)

[Install](#install) · [Features](#features) · [matugen](#matugen) · [Development](#development)

<img src="docs/screenshots/hero.png" alt="Mass home and Now Playing windows on an expressive shape background" width="100%">

</div>

## Features

<table>
<tr>
<td width="50%" valign="top">

### Live theming
Follows your matugen or DankMaterialShell palette as it changes, or themes itself from the
current album art. Shapes, springs and type come straight from the DMS design tokens.

</td>
<td width="50%" valign="top">

### Every player
Browse, search and control every Music Assistant player, adjust volumes, move a queue
from one speaker to another, and group speakers from the Players page.

</td>
</tr>
<tr>
<td valign="top">

### This PC as a speaker
Registers the computer as a Music Assistant player through Sendspin. It can run as a systemd
user service, so music keeps playing after you close the window.

</td>
<td valign="top">

### Lyrics
Time-synced. Click a line to jump to it.

</td>
</tr>
<tr>
<td valign="top">

### AI Radio
AI DJ hosts per queue, live "on air" captions and stations. Needs the server's AI Radio plugin.

</td>
<td valign="top">

### Desktop integration
MPRIS, notifications with cover art, a tray icon (StatusNotifierItem), start on login and media
shortcuts. The MASS menu adds sleep timer, audio overlay, announcements, autoplay, crossfade and
save queue as playlist, all matching the Music Assistant web UI.

</td>
</tr>
<tr>
<td valign="top">

### Library
Tracks, Podcasts, Genres and Browse tabs next to albums, artists, playlists and radio. Each tab
has its own search, favorites switch, sort and filters, and remembers them between launches.

</td>
<td valign="top">

### Pages and menus
Genre and podcast pages, a heart on album, playlist and artist pages, and favorite, Go to
album / artist and Add to playlist in every media menu.

</td>
</tr>
<tr>
<td valign="top">

### Giant playlists
Playlists with thousands of tracks open instantly, scroll smoothly and have a "Filter tracks"
box. Small playlists keep the drag-to-reorder editor.

</td>
<td valign="top">

### Server admin, one click away
"Open in web app" jumps to the Music Assistant pages Mass doesn't reimplement: settings,
providers, users, statistics, party mode and smart playlists.

</td>
</tr>
</table>

### Now Playing

<img src="docs/screenshots/now-playing.png" alt="Now Playing with synced lyrics" width="100%">

<table>
<tr>
<td width="50%"><img src="docs/screenshots/queue.png" alt="Up next, with DJ breaks between tracks"></td>
<td width="50%"><img src="docs/screenshots/ai-dj.png" alt="AI DJ transcript"></td>
</tr>
<tr>
<td align="center"><sub><b>Up next</b>: the queue with DJ breaks slotted between tracks</sub></td>
<td align="center"><sub><b>AI DJ</b>: everything your host has said on air</sub></td>
</tr>
</table>

### Browse

<table>
<tr>
<td width="50%"><img src="docs/screenshots/home.png" alt="Home"></td>
<td width="50%"><img src="docs/screenshots/library.png" alt="Library"></td>
</tr>
<tr>
<td align="center"><sub><b>Home</b>: what's playing, jump back in, recently added, playlists</sub></td>
<td align="center"><sub><b>Library</b>: albums, artists, playlists and radio</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/artist.png" alt="Artist page"></td>
<td><img src="docs/screenshots/album.png" alt="Album page"></td>
</tr>
<tr>
<td align="center"><sub><b>Artists</b>: an expressive hero with their popular tracks</sub></td>
<td align="center"><sub><b>Albums</b>: play, shuffle or queue the lot</sub></td>
</tr>
</table>

### Players and AI Radio

<table>
<tr>
<td width="50%"><img src="docs/screenshots/players.png" alt="Players dialog"></td>
<td width="50%"><img src="docs/screenshots/ai-radio.png" alt="AI Radio"></td>
</tr>
<tr>
<td align="center"><sub><b>Players</b>: pick what Mass controls, set volumes, transfer playback</sub></td>
<td align="center"><sub><b>AI Radio</b>: DJ hosts, stations and recent breaks</sub></td>
</tr>
</table>

## Requirements

A Music Assistant server (schema 28+). You sign in with your MA account, and the token is stored
in your keyring.

Everything works on older servers (Music Assistant 2.10 is schema 65); newer features simply
stay hidden until the server supports them:

| Schema | Needed for |
|--------|------------|
| 86 | Typed library sorting (older servers use legacy ordering) |
| 93 | Paged playlists and podcast episodes, server-side track filter |
| 94 | Paged Browse folders (older servers load a folder in one go) |
| 98 | Random sort |
| 101 | Genre and played-only filters; artist Popular, Appears on and Similar artists; lazily loaded recommendation rows |

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

**Sandbox access.** This Flatpak is not on Flathub because it needs `org.freedesktop.Flatpak` to run
commands on the host: `matugen` for album-art colors, `systemctl --user` for the background player,
and `flatpak-spawn` for updates. It writes a `mass-sendspin.service` user unit into
`~/.config/systemd/user` and shares `~/.config/mass` with a native install. Those permissions are what
let music keep playing after the window closes; install from this project's own repo if that trade-off
is acceptable.

<details>
<summary>Build the Flatpak yourself</summary>

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51
flatpak run org.flatpak.Builder --user --install --force-clean build-dir flatpak/io.github.mass_matugen.Mass.yml
```

This local build keeps the same host access described above (matugen, `systemctl --user`, and the
shared `~/.config/mass`).

</details>

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

### Screenshots

The images above come from two scripts. Run them against a connected server with something queued:

```sh
.venv/bin/python tools/screenshots/shoot.py /tmp/mass-shots 1400 900   # one PNG per view
python tools/screenshots/frame.py /tmp/mass-shots docs/screenshots      # expressive frames (needs rsvg-convert)
```
