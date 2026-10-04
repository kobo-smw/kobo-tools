#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Builds the tools pins.py names, from their pinned sources.

    build.py sources --out dist          source archives, one per tool
    build.py build TOOL... --platform P --sources dist --out dist
    build.py test --platform P dist      runs each build of P once
    build.py manifest --release TAG dist manifest.json and SHA256SUMS
    build.py pins dist/manifest.json     the table Kobo pins, as TOML
    build.py compare A B                 which files two archives differ in

Every archive is a gzipped tar with one top-level folder, its entries
sorted, and their times, owners, and permissions fixed, so it depends on
its contents alone. Each build is made from the source archive published
beside it, extracted, never from a checkout. Needs Python 3.9 and, to
build, git, CMake, a C++ compiler, and the .NET SDK pins.py names.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import pins

HERE = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/kobo-smw/kobo-tools"


def log(*args):
    print(*args, flush=True)


def run(cmd, cwd=None, env=None, capture=False):
    log("+", " ".join(str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        universal_newlines=capture,
    )
    return result.stdout if capture else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# Archives ---------------------------------------------------------------


def _info(name, size=0, mode=0o644, kind=tarfile.REGTYPE):
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.type = kind
    info.mtime = pins.SOURCE_DATE_EPOCH
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_archive(out, entries):
    """Writes `entries` ({archive path: (bytes, executable)}, or None for
    a folder) to `out`, a .tar.gz whose bytes depend on the entries alone."""
    names = sorted(n for n, e in entries.items() if e is not None)
    dirs = {n for n, e in entries.items() if e is None}
    for name in names:
        parts = name.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(dirs | set(names)):
            if name in dirs:
                tar.addfile(_info(name + "/", mode=0o755, kind=tarfile.DIRTYPE))
            else:
                data, executable = entries[name]
                info = _info(name, len(data), 0o755 if executable else 0o644)
                tar.addfile(info, io.BytesIO(data))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(buffer.getvalue())
    log(f"wrote {out} ({out.stat().st_size} bytes, sha256 {sha256(out)})")


def folder_entries(folder, prefix, skip=lambda relative: False):
    """The files under `folder` as archive entries under `prefix`."""
    entries = {}
    folder = Path(folder)
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if path.is_symlink():
            raise SystemExit(f"{path}: archives hold no links")
        if skip(relative):
            continue
        if path.is_dir():
            # Kept even when empty: a tool may expect the folder.
            entries[f"{prefix}/{relative}"] = None
            continue
        executable = bool(path.stat().st_mode & stat.S_IXUSR)
        entries[f"{prefix}/{relative}"] = (path.read_bytes(), executable)
    return entries


def extract(archive, into):
    """Extracts one of this repository's archives, refusing anything that
    would land outside `into`."""
    into = Path(into).resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (into / member.name).resolve()
            if into not in target.parents and target != into:
                raise SystemExit(f"{archive}: {member.name} is outside the archive")
            if not (member.isfile() or member.isdir()):
                raise SystemExit(f"{archive}: {member.name} is not a file or folder")
        tar.extractall(into)


# Sources ----------------------------------------------------------------


def stem(tool):
    """The name a tool's files start with: its key, or `stem` for a second
    version of a tool (`pixi-1.42` is `pixi`)."""
    return pins.TOOLS[tool].get("stem", tool)


def source_name(tool):
    return f"{stem(tool)}-{pins.TOOLS[tool]['version']}-source"


def build_name(tool, plat):
    return f"{stem(tool)}-{pins.TOOLS[tool]['version']}-{plat}"


def excluded(path, excludes):
    return any(path == e or path.startswith(e + "/") for e in excludes)


def download(url, expected, out):
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise SystemExit(f"{url}: sha256 {digest}, expected {expected}")
    Path(out).write_bytes(data)


def git_tree(name, repository, commit, tag, work):
    """The files of `repository` at `commit` ({path: (bytes, executable)}),
    after checking that `tag` still names that commit."""
    checkout = work / f"{name}-checkout"
    run(["git", "init", "-q", checkout])
    run(["git", "-C", checkout, "fetch", "-q", "--depth", "1", repository, commit])
    run(["git", "-C", checkout, "checkout", "-q", "FETCH_HEAD"])
    head = run(["git", "-C", checkout, "rev-parse", "HEAD"], capture=True).strip()
    if head != commit:
        raise SystemExit(f"{name}: fetched {head}, pinned {commit}")
    # The tag must still name the pinned commit, so the pin says what
    # upstream calls it.
    refs = run(["git", "ls-remote", repository, f"refs/tags/{tag}*"], capture=True)
    tagged = {line.split("\t")[1]: line.split("\t")[0] for line in refs.splitlines()}
    peeled = tagged.get(f"refs/tags/{tag}^{{}}", tagged.get(f"refs/tags/{tag}"))
    if peeled != commit:
        raise SystemExit(f"{name}: tag {tag} is {peeled}, pinned {commit}")
    if (checkout / ".gitmodules").exists():
        raise SystemExit(f"{name}: submodules are not supported")
    archive = subprocess.run(
        ["git", "-C", str(checkout), "archive", "--format=tar", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise SystemExit(f"{name}: {member.name} is a link; not supported")
            files[member.name] = (tar.extractfile(member).read(), bool(member.mode & 0o111))
    return files


def make_source(tool, out, work):
    pin = pins.TOOLS[tool]
    prefix = source_name(tool)
    entries = {}
    left_out = []
    for path, entry in git_tree(tool, pin["repository"], pin["commit"], pin["tag"], work).items():
        if excluded(path, pin["exclude"]):
            left_out.append(path)
        else:
            entries[f"{prefix}/{path}"] = entry
    deps = pin.get("dependencies", [])
    for dep in deps:
        if "url" in dep:
            path = work / dep["file"]
            download(dep["url"], dep["sha256"], path)
            entries[f"{prefix}/kobo-tools/deps/{dep['file']}"] = (path.read_bytes(), False)
        else:
            tree = git_tree(dep["name"], dep["repository"], dep["commit"], dep["tag"], work)
            for path, entry in tree.items():
                entries[f"{prefix}/kobo-tools/deps/{dep['file']}/{path}"] = entry
    notes = [
        f"{pin['name']} {pin['version']}: the source kobo-tools ({REPOSITORY}) builds it from.",
        "",
        f"Upstream: {pin['repository']}",
        f"Tag {pin['tag']}, commit {pin['commit']}.",
        f"Licence: {pin['licence']}.",
        "",
        "This is upstream's tree at that commit, unchanged,",
    ]
    if pin["exclude"]:
        notes.append("less these, which are neither built nor published:")
        for e in pin["exclude"]:
            count = sum(1 for f in left_out if excluded(f, [e]))
            notes.append(f"  {e} ({count} file{'' if count == 1 else 's'})")
    if deps:
        notes.append("with, in kobo-tools/deps, the source of what the build takes in:")
        for d in deps:
            origin = (
                f"{d['url']}, sha256 {d['sha256']}"
                if "url" in d
                else f"{d['repository']} tag {d['tag']}, commit {d['commit']}"
            )
            notes.append(f"  {d['file']}: {d['name']} {d['version']} ({d['licence']}), {origin}")
            if d.get("note"):
                notes.append(f"    {d['note']}")
    if pin.get("source_note"):
        notes += [""] + pin["source_note"]
    notes += ["", "build.py in kobo-tools is how it is built."]
    entries[f"{prefix}/kobo-tools/SOURCE.txt"] = ("\n".join(notes).encode() + b"\n", False)
    write_archive(Path(out) / f"{prefix}.tar.gz", entries)


def cmd_sources(args):
    work = Path(tempfile.mkdtemp(prefix="kobo-tools-"))
    try:
        for tool in args.tools or list(pins.TOOLS):
            if tool not in pins.TOOLS:
                raise SystemExit(f"{tool}: not a tool pins.py names")
            make_source(tool, args.out, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# Builds -----------------------------------------------------------------


def exe(name):
    return name + ".exe" if sys.platform == "win32" else name


def library_name():
    if sys.platform == "win32":
        return "asar.dll"
    if sys.platform == "darwin":
        return "libasar.dylib"
    return "libasar.so"


def host_platform():
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[machine]
    system = {"linux": "linux", "win32": "windows", "darwin": "macos"}[sys.platform]
    return f"{system}-{arch}"


def find_one(root, names):
    """The one real file under `root` named one of `names`."""
    found = [
        p
        for p in Path(root).rglob("*")
        if p.name in names and p.is_file() and not p.is_symlink()
    ]
    if len(found) != 1:
        raise SystemExit(f"expected one of {names} under {root}, found {found}")
    return found[0]


def version_line(cmd):
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return out.stdout.strip().splitlines()[0]
    except (OSError, IndexError):
        return None


class Builder:
    def __init__(self, plat, sources, out, work):
        self.plat = plat
        self.sources = Path(sources)
        self.out = Path(out)
        self.work = Path(work)
        self.env = dict(os.environ)
        self.env["SOURCE_DATE_EPOCH"] = str(pins.SOURCE_DATE_EPOCH)
        self.toolchain = []
        if sys.platform == "win32":
            # Deterministic objects and images: no timestamps in them.
            flags = "/Brepro"
            self.env["CFLAGS"] = flags
            self.env["CXXFLAGS"] = flags
            self.env["LDFLAGS"] = flags
        else:
            # No build folder paths in the output.
            flags = f"-ffile-prefix-map={self.work}=."
            self.env["CFLAGS"] = flags
            self.env["CXXFLAGS"] = flags
            if sys.platform == "darwin":
                self.env["ZERO_AR_DATE"] = "1"
            else:
                # libstdc++ inside, so the builds need no particular one.
                self.env["LDFLAGS"] = "-static-libstdc++ -static-libgcc"

    def cmake_args(self):
        args = ["-DCMAKE_BUILD_TYPE=Release"]
        if sys.platform == "darwin":
            arch = {"macos-arm64": "arm64", "macos-x64": "x86_64"}[self.plat]
            args += [f"-DCMAKE_OSX_ARCHITECTURES={arch}", "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0"]
        return args

    def cmake(self, source, build, extra):
        run(["cmake", "-S", source, "-B", build] + self.cmake_args() + extra, env=self.env)
        run(["cmake", "--build", build, "--config", "Release", "--parallel"], env=self.env)
        # The compiler CMake found, as it identified it.
        for path in sorted(Path(build, "CMakeFiles").glob("*/CMakeCXXCompiler.cmake")):
            found = dict(re.findall(r'set\(CMAKE_CXX_COMPILER_(ID|VERSION) "([^"]*)"\)', path.read_text()))
            self.note_tool("C++ compiler", f"{found.get('ID')} {found.get('VERSION')}")
        self.note_tool("CMake", version_line(["cmake", "--version"]))

    def note_tool(self, name, version):
        entry = f"{name}: {version}"
        if version and entry not in self.toolchain:
            self.toolchain.append(entry)

    def unpack_source(self, tool):
        into = self.work / "src"
        into.mkdir(parents=True, exist_ok=True)
        extract(self.sources / f"{source_name(tool)}.tar.gz", into)
        return into / source_name(tool)

    def provenance(self, tool, contents, notes=()):
        pin = pins.TOOLS[tool]
        lines = [
            f"{pin['name']} {pin['version']} for {self.plat}, built by kobo-tools ({REPOSITORY}).",
            "",
            f"Upstream: {pin['repository']}, tag {pin['tag']}, commit {pin['commit']}.",
            f"Licence: {pin['licence']}; the licence texts are in this folder.",
            f"Source: {source_name(tool)}.tar.gz, in the same release as this build: the exact",
            "source it was built from, with what it leaves out and why.",
            "",
            "Contents:",
        ]
        lines += [f"  {c}" for c in contents]
        if notes:
            lines += [""] + list(notes)
        lines += ["", "Built with:"] + [f"  {t}" for t in self.toolchain]
        return ("\n".join(lines) + "\n").encode()

    def package(self, tool, entries):
        write_archive(self.out / f"{build_name(tool, self.plat)}.tar.gz", entries)

    def licence_entries(self, tool, source, root):
        return {
            f"{root}/{name}": (Path(source, name).read_bytes(), False)
            for name in pins.TOOLS[tool]["licence_files"]
        }

    # Asar: the executable and the shared library.
    def build_asar(self):
        source = self.unpack_source("asar")
        build = self.work / "build-asar"
        self.cmake(
            source / "src",
            build,
            [
                "-DASAR_GEN_EXE=ON",
                "-DASAR_GEN_DLL=ON",
                "-DASAR_GEN_LIB=OFF",
                "-DASAR_GEN_EXE_TEST=OFF",
                "-DASAR_GEN_DLL_TEST=OFF",
            ],
        )
        root = build_name("asar", self.plat)
        program = find_one(build, [exe("asar")])
        library = find_one(build, self.library_files())
        entries = self.licence_entries("asar", source, root)
        entries[f"{root}/{exe('asar')}"] = (program.read_bytes(), True)
        entries[f"{root}/{library_name()}"] = (library.read_bytes(), False)
        entries[f"{root}/README.md"] = ((source / "README.md").read_bytes(), False)
        entries[f"{root}/KOBO-TOOLS.txt"] = (
            self.provenance(
                "asar",
                [
                    f"{exe('asar')}: the command-line assembler",
                    f"{library_name()}: the shared library (API 3.03), which PIXI, UberASM Tool,",
                    "  GPS, AddmusicK, and Kobo load",
                ],
            ),
            False,
        )
        self.package("asar", entries)

    def library_files(self):
        if sys.platform == "win32":
            return ["asar.dll"]
        if sys.platform == "darwin":
            return ["libasar.3.3.0.dylib"]
        return ["libasar.so.3.3.0"]

    # PIXI: the program and the files its releases carry, without the CFG
    # editor, and without Asar's library, which goes beside it from the
    # Asar build.
    def build_pixi(self, tool="pixi"):
        source = self.unpack_source(tool)
        asar = self.unpack_source("asar")
        deps = self.work / "pixi-deps"
        deps.mkdir(exist_ok=True)
        run(["cmake", "-E", "tar", "xf", source / "kobo-tools" / "deps" / "json.tar.xz"], cwd=deps)
        # PIXI's configure step writes the CFG editor's JSON constants too.
        (source / "src" / "CFG Editor" / "CFG Editor" / "Json").mkdir(parents=True, exist_ok=True)
        build = self.work / "build-pixi"
        self.cmake(
            source,
            build,
            [
                "-DPIXI_BUILD_TESTS=OFF",
                "-DPIXI_BUILD_DLL=OFF",
                "-DPIXI_BUILD_LIB=OFF",
                "-DPIXI_BUILD_EXE=ON",
                "-DPIXI_DISABLE_SANITIZERS=ON",
                "-DFETCHCONTENT_FULLY_DISCONNECTED=ON",
                f"-DFETCHCONTENT_SOURCE_DIR_ASAR={asar}",
                f"-DFETCHCONTENT_SOURCE_DIR_JSON={deps / 'json'}",
            ],
        )
        root = build_name(tool, self.plat)
        program = find_one(build, [exe("pixi")])
        entries = self.licence_entries(tool, source, root)
        entries[f"{root}/{exe('pixi')}"] = (program.read_bytes(), True)
        entries.update(pixi_release_files(source, root))
        entries[f"{root}/KOBO-TOOLS.txt"] = (
            self.provenance(
                tool,
                [
                    f"{exe('pixi')}, and the files PIXI's own release zip carries (its zip.py),",
                    "  with the documentation as Markdown",
                ],
                [
                    "Left out: the CFG editor, whose resources are Nintendo's, and Asar's",
                    f"library: put {library_name()} from the Asar {pins.TOOLS['asar']['version']} build of this release",
                    "beside the program.",
                ],
            ),
            False,
        )
        self.package(tool, entries)

    # UberASM Tool: a self-contained x64 (or arm64) build with its files,
    # which loads Asar's native library from beside it.
    def build_uberasm(self):
        source = self.unpack_source("uberasm")
        rid = {
            "linux-x64": "linux-x64",
            "windows-x64": "win-x64",
            "macos-arm64": "osx-arm64",
            "macos-x64": "osx-x64",
        }[self.plat]
        target = "arm64" if rid.endswith("arm64") else "x64"
        env = dict(self.env)
        env.update(
            {
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_NOLOGO": "1",
                "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT": "1",
                "NUGET_PACKAGES": str(self.work / "nuget"),
            }
        )
        # The SDK the pins name, and no other.
        (source / "global.json").write_text(
            json.dumps({"sdk": {"version": pins.DOTNET_SDK, "rollForward": "disable"}})
        )
        publish = self.work / "publish-uberasm"
        run(
            [
                "dotnet",
                "publish",
                source / "UberASMTool" / "UberASMTool.csproj",
                "-c",
                "Release",
                "-r",
                rid,
                "--self-contained",
                "true",
                f"-p:PlatformTarget={target}",
                "-p:PublishSingleFile=true",
                "-p:IncludeNativeLibrariesForSelfExtract=true",
                "-p:EnableCompressionInSingleFile=true",
                "-p:InvariantGlobalization=true",
                "-p:DebugType=none",
                "-p:ContinuousIntegrationBuild=true",
                "-p:Deterministic=true",
                "-o",
                publish,
            ],
            env=env,
        )
        self.note_tool(".NET SDK", pins.DOTNET_SDK)
        root = build_name("uberasm", self.plat)
        entries = self.licence_entries("uberasm", source, root)
        for path in sorted(publish.iterdir()):
            if path.suffix in (".pdb",) or path.name == "asar.dll":
                continue
            entries[f"{root}/{path.name}"] = (path.read_bytes(), path.name in ("UberASMTool", "UberASMTool.exe"))
        # What the tool reads beside it, less what only keeps a folder in
        # git, which it would take for a library file.
        entries.update(
            folder_entries(source / "assets", root, skip=lambda r: any(p.startswith(".") for p in r.split("/")))
        )
        entries.update(dotnet_notices(self.work / "nuget", root))
        entries[f"{root}/README.md"] = ((source / "README.md").read_bytes(), False)
        entries[f"{root}/KOBO-TOOLS.txt"] = (
            self.provenance(
                "uberasm",
                [
                    f"{exe('UberASMTool')}: built for {target} and self-contained, so it needs no .NET",
                    "  installed; the .NET runtime inside is MIT-licensed, and third-party/",
                    "  has the notices of everything the build bundles",
                    "the files UberASM Tool's release carries (upstream's assets/ folder)",
                ],
                [
                    "Upstream builds for 32-bit Windows only. This build is upstream's source",
                    f"unchanged, published for {target} with the options build.py gives.",
                    f"Asar's library is not included: put {library_name()} from the Asar",
                    f"{pins.TOOLS['asar']['version']} build of this release beside the program.",
                ],
            ),
            False,
        )
        self.package("uberasm", entries)


def pixi_release_files(source, root):
    """What PIXI's zip.py puts in a release, but the program, the CFG
    editor, and Asar's library; the documents as Markdown."""

    def keep_header(folder):
        return rf"^{folder}/(?!_header\.asm).+$"

    filters = {
        "routines": [r"^routines/\."],
        "extended": [r"^extended/(?!(?:routines|_header)\.asm).+$"],
    }
    entries = {}
    for folder in [
        "sprites",
        "shooters",
        "generators",
        "cluster",
        "extended",
        "routines",
        "misc_sprites/bounce",
        "misc_sprites/minorextended",
        "misc_sprites/score",
        "misc_sprites/smoke",
        "misc_sprites/spinningcoin",
    ]:
        excludes = filters.get(folder, [keep_header(folder)])
        for path in sorted((source / folder).rglob("*")):
            relative = path.relative_to(source).as_posix()
            if path.is_dir() or any(re.search(e, relative) for e in excludes):
                continue
            entries[f"{root}/{relative}"] = (path.read_bytes(), False)
    for name in [
        "main.asm",
        "sa1def.asm",
        "cluster.asm",
        "extended.asm",
        "minorextended.asm",
        "score.asm",
        "bounce.asm",
        "spinningcoin.asm",
        "smoke.asm",
        "pointer_caller.asm",
        "DefaultSize.bin",
        "spritetool_clean.asm",
        "ExtraDefines/placeholder.txt",
        "ExtraHijacks/placeholder.txt",
    ]:
        entries[f"{root}/asm/{name}"] = ((source / "asm" / name).read_bytes(), False)
    for name in ["list.txt", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "removedResources.txt"]:
        entries[f"{root}/{name}"] = ((source / name).read_bytes(), False)
    return entries


def dotnet_notices(packages, root):
    """The licence and notice files of every NuGet package the publish
    restored (the runtime among them), and Pidgin's, whose package has
    none, from this repository."""
    entries = {}
    for folder in sorted(Path(packages).iterdir()) if Path(packages).exists() else []:
        for version in sorted(folder.iterdir()):
            for name in ("LICENSE.TXT", "LICENSE.txt", "LICENSE", "THIRD-PARTY-NOTICES.TXT"):
                path = version / name
                if path.is_file():
                    entries[f"{root}/third-party/{folder.name}-{version.name}/{name}"] = (path.read_bytes(), False)
    for path in sorted((HERE / "licences").iterdir()):
        entries[f"{root}/third-party/{path.name}"] = (path.read_bytes(), False)
    return entries


def cmd_build(args):
    plat = args.platform or host_platform()
    if plat != host_platform():
        raise SystemExit(f"building for {plat} on {host_platform()} is not supported")
    work = Path(tempfile.mkdtemp(prefix="kobo-tools-"))
    try:
        for tool in args.tools:
            builder = Builder(plat, args.sources, args.out, work / tool)
            kind = pins.TOOLS[tool].get("builder", tool)
            if kind == tool:
                getattr(builder, f"build_{kind}")()
            else:
                getattr(builder, f"build_{kind}")(tool)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


# Smoke tests ------------------------------------------------------------


def cmd_test(args):
    """Runs each tool's build for the platform once: Asar patches an image,
    PIXI prints its version, UberASM Tool inserts level code into an image
    with no game data, with the Asar build's library beside it."""
    plat = args.platform or host_platform()
    dist = Path(args.dist)
    work = Path(tempfile.mkdtemp(prefix="kobo-tools-test-"))
    try:
        roots = {}
        for tool in pins.TOOLS:
            extract(dist / f"{build_name(tool, plat)}.tar.gz", work)
            roots[tool] = work / build_name(tool, plat)
        asar = roots["asar"]

        patch = work / "patch.asm"
        patch.write_text("lorom\norg $008000\ndb $4B,$4F,$42,$4F\n")
        rom = work / "asar.sfc"
        rom.write_bytes(bytes(0x80000))
        run([asar / exe("asar"), "--no-title-check", patch, rom])
        if rom.read_bytes()[:4] != b"KOBO":
            raise SystemExit("Asar did not patch the image")

        for tool, pin in pins.TOOLS.items():
            if pin.get("builder", tool) != "pixi":
                continue
            version = run([roots[tool] / exe("pixi"), "--version"], capture=True)
            if pin["version"] not in version:
                raise SystemExit(f"PIXI {pin['version']} printed {version!r}")

        if plat.startswith("linux"):
            check_glibc(roots.values())

        uberasm = roots["uberasm"]
        shutil.copy(asar / library_name(), uberasm / library_name())
        (uberasm / "list.txt").write_text(
            "verbose: off\nlevel:\n105 kobo_test.asm\noverworld:\ngamemode:\n"
            "global: other/global_code.asm\nstatusbar: other/status_code.asm\n"
            "macrolib: other/macro_library.asm\nfreeram: $7FAC80\n"
        )
        (uberasm / "level" / "kobo_test.asm").write_text("main:\n    LDA #$42\n    STA $0DBF\n    RTL\n")
        image = bytearray(0x100000)
        image[0x7FC0:0x7FD5] = b"SUPER MARIOWORLD     "
        image[0x7FD5] = 0x20
        image[0x7FD7] = 0x0A
        (uberasm / "rom.sfc").write_bytes(bytes(image))
        env = dict(os.environ)
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        if sys.platform == "darwin":
            env["DYLD_LIBRARY_PATH"] = str(uberasm)
        elif sys.platform != "win32":
            env["LD_LIBRARY_PATH"] = str(uberasm)
        run([uberasm / exe("UberASMTool"), "list.txt", "rom.sfc"], cwd=uberasm, env=env)
        code = bytes([0xA9, 0x42, 0x8D, 0xBF, 0x0D, 0x6B])
        if code not in (uberasm / "rom.sfc").read_bytes():
            raise SystemExit("UberASM Tool did not insert the level code")
        log(f"{plat}: every build ran")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_glibc(roots):
    """Fails if a Linux program or library needs a glibc newer than
    pins.MAX_GLIBC, which would keep it off older distributions."""
    limit = tuple(int(x) for x in pins.MAX_GLIBC.split("."))
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            if not data.startswith(b"\x7fELF"):
                continue
            needed = {tuple(int(x) for x in m) for m in re.findall(rb"GLIBC_(\d+)\.(\d+)", data)}
            newest = max(needed, default=(0, 0))
            log(f"{path.name}: needs glibc {newest[0]}.{newest[1]}")
            if newest > limit:
                raise SystemExit(f"{path} needs glibc {newest[0]}.{newest[1]}, past {pins.MAX_GLIBC}")


# The release ------------------------------------------------------------


def cmd_manifest(args):
    dist = Path(args.dist)
    manifest = {
        "format": 1,
        "release": args.release,
        "repository": REPOSITORY,
        # The commit of this repository the release was built from.
        "commit": os.environ.get("GITHUB_SHA", ""),
        "url": f"{REPOSITORY}/releases/download/{args.release}/",
        "tools": {},
    }
    for tool, pin in pins.TOOLS.items():
        source = dist / f"{source_name(tool)}.tar.gz"
        entry = {
            "name": pin["name"],
            "version": pin["version"],
            "licence": pin["licence"],
            "upstream": {"repository": pin["repository"], "tag": pin["tag"], "commit": pin["commit"]},
            "source": {"file": source.name, "sha256": sha256(source), "size": source.stat().st_size},
            "builds": {},
        }
        for plat in pins.PLATFORMS:
            path = dist / f"{build_name(tool, plat)}.tar.gz"
            if not path.exists():
                raise SystemExit(f"{path} is missing")
            entry["builds"][plat] = {
                "file": path.name,
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "root": build_name(tool, plat),
            }
        manifest["tools"][tool] = entry
    (dist / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sums = [f"{sha256(p)}  {p.name}" for p in sorted(dist.glob("*.tar.gz"))]
    sums.append(f"{sha256(dist / 'manifest.json')}  manifest.json")
    (dist / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    log((dist / "SHA256SUMS").read_text())


def cmd_pins(args):
    """The table Kobo embeds (crates/kobo-core/src/tools/pinned.toml)."""
    manifest = json.loads(Path(args.manifest).read_text())
    lines = [
        "# The builds of the licensed tools Kobo fetches, from kobo-tools's",
        f"# release {manifest['release']} ({manifest['repository']}). Written by its",
        "# `build.py pins manifest.json`; do not edit by hand.",
        f'release = "{manifest["release"]}"',
        f'url = "{manifest["url"]}"',
    ]
    for tool, entry in manifest["tools"].items():
        # A key with a dot (pixi-1.42) is quoted.
        key = f'"{tool}"' if "." in tool else tool
        lines += [
            "",
            f"[tools.{key}]",
            f'name = "{entry["name"]}"',
            f'version = "{entry["version"]}"',
            f'licence = "{entry["licence"]}"',
            f'commit = "{entry["upstream"]["commit"]}"',
        ]
        for plat, build in entry["builds"].items():
            lines += [
                "",
                f"[tools.{key}.builds.{plat}]",
                f'file = "{build["file"]}"',
                f'sha256 = "{build["sha256"]}"',
                f"size = {build['size']}",
                f'root = "{build["root"]}"',
            ]
    print("\n".join(lines))


def cmd_compare(args):
    """Compares two archives file by file; exits 1 if they differ."""

    def members(path):
        with tarfile.open(path, "r:gz") as tar:
            return {
                m.name: (m.mode, hashlib.sha256(tar.extractfile(m).read()).hexdigest() if m.isfile() else None)
                for m in tar.getmembers()
            }

    a, b = members(args.a), members(args.b)
    differ = sorted(n for n in a.keys() | b.keys() if a.get(n) != b.get(n))
    for name in differ:
        log(f"differs: {name}")
    if differ:
        raise SystemExit(1)
    log(f"{args.a} and {args.b} hold the same files")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sources")
    p.add_argument("tools", nargs="*", help="default: all of them")
    p.add_argument("--out", default="dist")
    p.set_defaults(func=cmd_sources)
    p = sub.add_parser("build")
    p.add_argument("tools", nargs="+", choices=list(pins.TOOLS))
    p.add_argument("--platform", choices=pins.PLATFORMS)
    p.add_argument("--sources", default="dist")
    p.add_argument("--out", default="dist")
    p.add_argument("--keep", action="store_true", help="keep the work folder")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("test")
    p.add_argument("dist", nargs="?", default="dist")
    p.add_argument("--platform", choices=pins.PLATFORMS)
    p.set_defaults(func=cmd_test)
    p = sub.add_parser("manifest")
    p.add_argument("dist", nargs="?", default="dist")
    p.add_argument("--release", required=True)
    p.set_defaults(func=cmd_manifest)
    p = sub.add_parser("pins")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_pins)
    p = sub.add_parser("compare")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_compare)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
