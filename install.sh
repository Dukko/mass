#!/usr/bin/env bash
# Installs Mass for the current user (no root needed).
set -euo pipefail
cd "$(dirname "$0")"

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
APPDIR="$PREFIX/mass"
VENV="$APPDIR/venv"
BIN="$HOME/.local/bin"
APP_ID=io.github.mass_matugen.Mass

echo "==> Checking system packages"
/usr/bin/python3 -c 'import gi; gi.require_version("Adw","1"); gi.require_version("Secret","1")' 2>/dev/null || {
  echo "Missing system deps. On Arch/CachyOS: sudo pacman -S --needed python-gobject gtk4 libadwaita libsecret"; exit 1; }
command -v matugen >/dev/null || echo "   (matugen not found: album-art theming will be unavailable)"

echo "==> Creating venv in $VENV"
/usr/bin/python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q .

echo "==> Installing launcher, desktop entry, icon"
mkdir -p "$BIN" "$PREFIX/applications" "$PREFIX/icons/hicolor/scalable/apps" "$APPDIR/matugen"
ln -sf "$VENV/bin/mass" "$BIN/mass"
install -m644 "data/$APP_ID.desktop" "$PREFIX/applications/$APP_ID.desktop"
install -m644 "data/icons/$APP_ID.svg" "$PREFIX/icons/hicolor/scalable/apps/$APP_ID.svg"
install -m644 data/matugen/mass-colors.json "$APPDIR/matugen/mass-colors.json"
update-desktop-database -q "$PREFIX/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$PREFIX/icons/hicolor" 2>/dev/null || true

echo "==> Installing local-player systemd user unit"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNITDIR"
sed "s|@SENDSPIN@|$VENV/bin/sendspin|" data/systemd/mass-sendspin.service > "$UNITDIR/mass-sendspin.service"
systemctl --user daemon-reload

cat <<MSG

Done. Run: mass   (or launch "Mass" from your app launcher)

Matugen: DankMaterialShell users need nothing more, because Mass reads DMS's palette live.
For plain matugen, add this to ~/.config/matugen/config.toml:

$(cat data/matugen/config-snippet.toml | sed 's/^/    /')
MSG
