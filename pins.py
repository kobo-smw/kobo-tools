# The upstream sources this repository builds, pinned by commit, and the
# toolchains it builds them with. Changing anything here is a new release.

# Every archive this repository makes gives its files this time, so an
# archive depends on its contents alone (2026-09-27 00:00:00 UTC).
SOURCE_DATE_EPOCH = 1790467200

# The platforms builds are made for, as Kobo names them.
PLATFORMS = ["linux-x64", "windows-x64", "macos-arm64", "macos-x64"]

TOOLS = {
    "asar": {
        "name": "Asar",
        "version": "1.91",
        "licence": "LGPL-3.0-or-later",
        "repository": "https://github.com/RPGHacker/asar",
        "tag": "v1.91",
        "commit": "6a6dbdaed8cb773e583052ce4f66375bd896c3a1",
        # Asar's licence, in its own words, and the texts it points to.
        "licence_files": [
            "LICENSE",
            "license-lgpl.txt",
            "license-gpl.txt",
            "license-wtfpl.txt",
        ],
        "exclude": [],
    },
    "pixi": {
        "name": "PIXI",
        "version": "1.43",
        "licence": "GPL-3.0",
        "repository": "https://github.com/JackTheSpades/SpriteToolSuperDelux",
        "tag": "1.43",
        "commit": "2aa70016e0016b31b0cbbab1ed77feabdc9af4d0",
        "licence_files": ["LICENSE"],
        # The CFG editor, a Windows program PIXI's releases bundle, holds
        # Nintendo's graphics, palettes, and a Map16 page in its resources,
        # and its tests hold more. It is neither built nor published.
        "exclude": ["src/CFG Editor"],
        # Compiled in, fetched by PIXI's CMake; published with its source.
        "dependencies": [
            {
                "name": "nlohmann/json",
                "version": "3.11.3",
                "licence": "MIT",
                "url": "https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz",
                "sha256": "d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d",
                "file": "json.tar.xz",
            }
        ],
    },
    # The PIXI the Romhack Races baserom's sprites are written for: a
    # project pins it with `[pixi] version = "1.42"`. Built as 1.43 is.
    "pixi-1.42": {
        "name": "PIXI",
        "version": "1.42",
        "stem": "pixi",
        "builder": "pixi",
        "licence": "GPL-3.0",
        "repository": "https://github.com/JackTheSpades/SpriteToolSuperDelux",
        "tag": "1.42",
        "commit": "034313b857c598cc3ccc2c991134d0dcd6975992",
        "licence_files": ["LICENSE"],
        "exclude": ["src/CFG Editor"],
        # 1.42 does not compile with Apple's C++ library; upstream's own fix,
        # after the tag, changes nothing PIXI does. Applied to the source
        # archive, line for line.
        "backports": [
            {
                "commit": "e85673f145ddedc381c3665807ea9bd7ba03b494",
                "title": "Fix compiler errors on macos",
                "edits": [
                    {
                        "file": "src/sprite.cpp",
                        "from": '    sprintf(to_write, "Pixi v%d.%d\\t", VERSION_MAJOR, VERSION_PARTIAL);',
                        "to": '    snprintf(to_write, sizeof(to_write), "Pixi v%d.%d\\t", VERSION_MAJOR, VERSION_PARTIAL);',
                    },
                    {
                        "file": "src/sprite.cpp",
                        "from": "            for (auto& path_part : rel.replace_extension()) {",
                        "to": "            for (const auto& path_part : rel.replace_extension()) {",
                    },
                ],
            }
        ],
        "dependencies": [
            {
                "name": "nlohmann/json",
                "version": "3.11.3",
                "licence": "MIT",
                "url": "https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz",
                "sha256": "d6c65aca6b1ed68e7a182f4757257b107ae403032760ed6ef121c9d55e81757d",
                "file": "json.tar.xz",
            }
        ],
    },
    "uberasm": {
        "name": "UberASM Tool",
        "version": "2.1",
        "licence": "GPL-3.0",
        "repository": "https://github.com/Fernap/UberASMTool",
        "tag": "2.1",
        "commit": "e238194c83cb7c6cfbab28e0c3ededb464a2def7",
        "licence_files": ["LICENSE"],
        # A 32-bit Windows build of Asar, which the x64 builds replace
        # with the native library beside them.
        "exclude": ["UberASMTool/asar.dll"],
        # The build restores Pidgin from NuGet and bundles it; its source
        # is published with UberASM Tool's.
        "dependencies": [
            {
                "name": "Pidgin",
                "version": "3.2.0",
                "licence": "MIT",
                "repository": "https://github.com/benjamin-hodgson/Pidgin",
                "tag": "v3.2.0",
                "commit": "4a3fccc9ef8ff0d33ce292186c42e77417e2bc84",
                "file": "Pidgin",
                "note": "The build takes the NuGet package of this release (Pidgin 3.2.0).",
            }
        ],
        "source_note": [
            "The self-contained builds also bundle the .NET runtime the pinned SDK",
            "brings (MIT; https://github.com/dotnet/runtime), which is the platform",
            "the program runs on; its notices are in each build's third-party/.",
        ],
    },
}

# The .NET SDK UberASM Tool is published with. It fixes the runtime a
# self-contained build bundles (8.0.31 for this SDK).
DOTNET_SDK = "8.0.425"

# The Linux builds run in this image, Debian 12, with its packages from
# this snapshot of the archive, so the compiler does not change under
# them. Its glibc is 2.36, but the builds may need nothing past
# MAX_GLIBC, which build.py's test checks.
LINUX_IMAGE = "debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251"
DEBIAN_SNAPSHOT = "20260918T000000Z"
# The newest glibc a Linux build may need: Ubuntu 22.04's.
MAX_GLIBC = "2.35"
