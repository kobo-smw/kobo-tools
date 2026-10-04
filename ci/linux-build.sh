#!/bin/sh
# Builds the Linux x64 tools in the pinned image, as CI does, from the
# source archives in dist/, into dist/:
#   docker run --rm -v "$PWD:/w" -w /w <pins.LINUX_IMAGE> ci/linux-build.sh
set -eu
cd "$(dirname "$0")/.."
ci/linux-deps.sh
git config --global --add safe.directory '*'
python3 build.py build asar pixi pixi-1.42 uberasm --platform linux-x64 --sources dist --out dist
python3 build.py test --platform linux-x64 dist
