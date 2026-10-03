#!/bin/sh
# Installs what build.py needs in the Linux build image (pins.LINUX_IMAGE):
# Debian's compiler and CMake from the snapshot pins.DEBIAN_SNAPSHOT names,
# so they are the same on every run, and the pinned .NET SDK.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
pin() {
    python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import pins; print(getattr(pins, sys.argv[2]))' "$here" "$1"
}
export DEBIAN_FRONTEND=noninteractive
# The image has no Python yet: read the snapshot from pins.py by hand.
snapshot=$(sed -n 's/^DEBIAN_SNAPSHOT = "\(.*\)"/\1/p' "$here/pins.py")
cat > /etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: http://snapshot.debian.org/archive/debian/$snapshot
Suites: bookworm bookworm-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/$snapshot
Suites: bookworm-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update -qq
apt-get -o Acquire::Retries=5 install -y -qq --no-install-recommends \
    ca-certificates curl git python3 cmake make g++ xz-utils libicu72 >/dev/null
sdk=$(pin DOTNET_SDK)
curl -fsSL --retry 5 https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
bash /tmp/dotnet-install.sh --version "$sdk" --install-dir /usr/local/dotnet --no-path
ln -sf /usr/local/dotnet/dotnet /usr/local/bin/dotnet
