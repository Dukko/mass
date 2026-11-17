#!/usr/bin/env bash
# One-time setup for the self-hosted Flatpak repo (GitHub Pages).
#  - creates a dedicated signing key (in a temp GnuPG home; your keyring is untouched)
#  - stores the private key as the GitHub Actions secret FLATPAK_GPG_KEY
#  - commits the public key to flatpak/mass-repo.gpg.b64
#  - enables GitHub Pages with "GitHub Actions" as the source
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

G=$(mktemp -d); trap 'rm -rf "$G"' EXIT
gpg --homedir "$G" --batch --passphrase '' --quick-gen-key "Mass Flatpak repo" ed25519 sign never
KEYID=$(gpg --homedir "$G" --list-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')

gpg --homedir "$G" --batch --armor --export-secret-keys "$KEYID" | gh secret set FLATPAK_GPG_KEY -R "$REPO"
gpg --homedir "$G" --export "$KEYID" | base64 -w0 > flatpak/mass-repo.gpg.b64
echo "==> secret FLATPAK_GPG_KEY set; public key written to flatpak/mass-repo.gpg.b64 ($KEYID)"

gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null
echo "==> GitHub Pages enabled (source: GitHub Actions)"

git add flatpak/mass-repo.gpg.b64
git commit -qm "flatpak: add repo signing public key"
git push -q
echo "==> pushed; CI will now publish https://$(echo "${REPO%%/*}" | tr A-Z a-z).github.io/${REPO#*/}/"
