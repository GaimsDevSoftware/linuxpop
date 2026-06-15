#!/usr/bin/env bash
# Rebuild + publish the self-hosted, GPG-signed LinuxPop Flatpak repo to GitHub
# Pages (https://gaimsdevsoftware.github.io/linuxpop-flatpak/). Run this after
# bumping the version / tagging a release. Idempotent.
#
#   bash packaging/flatpak/publish-flatpak-repo.sh
#
set -euo pipefail

KEYID="FEFAC70C5476018C5FE9A6D7E0075894590EE5D4"      # repo signing key
GNUPGHOME_DIR="$HOME/.config/linuxpop-flatpak-gpg"     # <- BACK THIS UP
REPO="$HOME/linuxpop-flatpak-repo"                     # local ostree repo
PAGES="$HOME/linuxpop-flatpak"                         # local clone of the Pages repo
PAGES_REMOTE="https://github.com/GaimsDevSoftware/linuxpop-flatpak.git"
MANIFEST="$(cd "$(dirname "$0")" && pwd)/io.github.GaimsDevSoftware.LinuxPop.yml"
APPID="io.github.GaimsDevSoftware.LinuxPop"
export GNUPGHOME="$GNUPGHOME_DIR"

command -v rsync >/dev/null || { echo "need rsync"; exit 1; }
gpg --list-secret-keys "$KEYID" >/dev/null 2>&1 || { echo "signing key $KEYID not found in $GNUPGHOME_DIR"; exit 1; }

echo "[1/5] building signed repo from $MANIFEST ..."
flatpak run org.flatpak.Builder --force-clean --ccache \
  --repo="$REPO" --gpg-sign="$KEYID" --gpg-homedir="$GNUPGHOME_DIR" \
  --default-branch=stable "$HOME/.cache/linuxpop-publish-build" "$MANIFEST"

echo "[2/5] updating repo metadata + static deltas ..."
flatpak build-update-repo --gpg-sign="$KEYID" --gpg-homedir="$GNUPGHOME_DIR" \
  --generate-static-deltas "$REPO"

echo "[3/5] syncing into Pages clone ..."
[ -d "$PAGES/.git" ] || git clone "$PAGES_REMOTE" "$PAGES"
mkdir -p "$PAGES/repo"
rsync -a --delete "$REPO/" "$PAGES/repo/"
touch "$PAGES/.nojekyll"

echo "[4/5] commit + push Pages ..."
git -C "$PAGES" config user.name  "$(git -C "$HOME/src/linuxpop" config user.name)"
git -C "$PAGES" config user.email "$(git -C "$HOME/src/linuxpop" config user.email)"
git -C "$PAGES" add -A
git -C "$PAGES" commit -m "Update Flatpak repo ($(git -C "$(dirname "$MANIFEST")/../.." describe --tags --always 2>/dev/null || echo update))" \
  && git -C "$PAGES" push || echo "  (nothing to push)"

echo "[5/5] single-file bundle (attach to a GitHub Release) ..."
flatpak build-bundle --gpg-sign="$KEYID" --gpg-homedir="$GNUPGHOME_DIR" \
  --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo \
  "$REPO" "/tmp/${APPID}.flatpak" "$APPID" stable
echo "    bundle: /tmp/${APPID}.flatpak"
echo
echo "done. Users install with:"
echo "  flatpak install --from https://gaimsdevsoftware.github.io/linuxpop-flatpak/linuxpop.flatpakref"
