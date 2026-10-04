# kobo-tools

Builds of the licensed tools [Kobo](https://github.com/kobo-smw/kobo-smw-editor) runs, made from pinned
upstream commits on CI and published as releases with their sources:

| Tool | Version | Licence | Upstream |
|---|---|---|---|
| Asar (program and shared library) | 1.91 | LGPL-3.0-or-later | [RPGHacker/asar](https://github.com/RPGHacker/asar) |
| PIXI | 1.43, and 1.42 | GPL-3.0 | [JackTheSpades/SpriteToolSuperDelux](https://github.com/JackTheSpades/SpriteToolSuperDelux) |
| UberASM Tool | 2.1 | GPL-3.0 | [Fernap/UberASMTool](https://github.com/Fernap/UberASMTool) |

for `linux-x64`, `windows-x64`, `macos-arm64`, and `macos-x64`. The commits, and everything
else a build depends on, are in [`pins.py`](pins.py). PIXI 1.43 is the one Kobo uses by
default; 1.42 (`pixi-1.42`, from `r2`) is for projects whose sprites are written for it, the
Romhack Races baserom's among them.

Kobo downloads the build for its platform on first use, checks it against the SHA-256 it
pins, and caches it. Anyone else can use them too: each is a `.tar.gz` of one folder.

AddmusicK, GPS, and SA-1 Pack have no licence and are never built, bundled, or rehosted
here.

## What a release holds

- `<tool>-<version>-<platform>.tar.gz`: the build. `KOBO-TOOLS.txt` inside says what it
  holds, where it came from, and what it was built with; the upstream licence texts are
  beside it.
- `<tool>-<version>-source.tar.gz`: the exact source the builds were made from: the
  upstream tree at the pinned commit, less what is listed in its `kobo-tools/SOURCE.txt`,
  and with the third-party source the build compiles in (PIXI's copy of nlohmann/json).
  Every build is made by unpacking this archive, never from a checkout.
- `manifest.json`: every file above with its SHA-256, size, platform, and upstream commit;
  `SHA256SUMS`; and `kobo-pinned.toml`, the table Kobo embeds.

The builds differ from upstream's releases in these ways, and only these:

- **PIXI** is built without its CFG editor, a Windows program whose resources are
  Nintendo's graphics, palettes, and a Map16 page; the editor's folder is left out of the
  source archive too. The rest of PIXI's release (its `asm/`, sprite, and routine folders,
  `list.txt`) is as its own `zip.py` packs it, with the documents as Markdown.
- **PIXI 1.42** has upstream's commit `e85673f` ("Fix compiler errors on macos", made after
  the 1.42 tag) applied to its source archive, without which it does not compile with
  Apple's C++ library: two lines of `src/sprite.cpp`, a `const` reference and `snprintf`
  for `sprintf`. What PIXI does is unchanged.
- **UberASM Tool** is published for x64 (arm64 on Apple silicon) instead of 32-bit Windows,
  self-contained, so it needs no .NET installed, and without the 32-bit `asar.dll` its
  repository carries. Its source is unchanged; `build.py` gives `dotnet publish` the
  options. The bundled .NET runtime is MIT-licensed; its notices and those of every package
  the build restored are in `third-party/`.
- **Asar's library is in the Asar build only.** PIXI and UberASM Tool load it from beside
  their program: put `libasar.so`, `libasar.dylib`, or `asar.dll` from the same release
  there (Kobo does).

## Building

```sh
python3 build.py sources --out dist        # needs git and the network
python3 build.py build asar pixi pixi-1.42 uberasm --sources dist --out dist
python3 build.py test dist
```

`build` needs CMake, a C++ compiler (MSVC on Windows), and the .NET SDK `pins.py` names. The
Linux builds are made in the pinned Debian image, with Debian's compiler from a pinned
snapshot of its archive:

```sh
docker run --rm -v "$PWD:/w" -w /w <pins.LINUX_IMAGE> ci/linux-build.sh
```

CI (`.github/workflows/build.yml`) does this for every platform on every push, and for a
tag `r<N>` publishes the release. Changing a pin is a new release; Kobo then pins that
release's hashes (`build.py pins manifest.json` writes its table).

## Reproducibility

Every archive's bytes depend on its contents alone: entries sorted, times fixed
(`SOURCE_DATE_EPOCH` in `pins.py`), owners and permissions normalised, gzip without a
timestamp. What the builds inside depend on:

- **Linux**: the pinned image and Debian snapshot fix the compiler, CMake, and libraries;
  build paths are mapped out of the output (`-ffile-prefix-map`), and libstdc++ is linked
  statically. A rebuild gives the same bytes. The builds need glibc 2.35 or later
  (Ubuntu 22.04, Debian 12), which the test checks.
- **Windows and macOS**: built on GitHub's runner images with their MSVC or Apple Clang,
  deterministic flags (`/Brepro`, `ZERO_AR_DATE`, `-ffile-prefix-map`). A rebuild on the
  same image version gives the same bytes; a later image with a newer compiler may not.
  The images cannot be pinned.
- **UberASM Tool**: .NET's compiler is deterministic, and the SDK is pinned (which fixes the
  runtime it bundles); NuGet packages are fetched by version, and checked by NuGet's own
  hashes.

`.github/workflows/verify.yml` rebuilds a release from its own source archives and compares
every build with the published one, file by file: monthly against the latest release, or on
demand. A Linux difference fails it; other platforms' differences are reported. For `r1`
(2026-10-03) every one of the twelve builds came out byte-identical.

## Licence

This repository's own files (`build.py`, `pins.py`, `ci/`, the workflows) are MPL-2.0. Each
tool keeps its own licence, and each build carries it.
