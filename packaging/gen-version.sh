#!/usr/bin/env bash
# Resolve the LinuxPop version for a *package build* and print it to stdout.
#
#   bash packaging/gen-version.sh            # resolve from git tag / metainfo
#   bash packaging/gen-version.sh 0.9.7      # force an explicit version
#
# Resolution order: explicit $1 > git tag (git describe) > newest metainfo
# <release> > 0.0.0. Callers bake the result into a _version.py next to main.py
# so installed copies (which have no .git) report the right version, e.g.:
#
#   printf 'VERSION = "%s"\n' "$(bash packaging/gen-version.sh)" > _version.py
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
META="$ROOT/packaging/io.github.GaimsDevSoftware.LinuxPop.metainfo.xml"

ver="${1:-}"

if [[ -z "$ver" ]]; then
    ver="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || true)"
    ver="${ver#v}"
fi

if [[ -z "$ver" && -f "$META" ]]; then
    ver="$(grep -oE '<release[^>]*version="[^"]+"' "$META" | head -1 \
           | sed -E 's/.*version="([^"]+)".*/\1/' || true)"
fi

[[ -z "$ver" ]] && ver="0.0.0"
printf '%s\n' "$ver"
