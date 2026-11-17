"""Build or verify a deterministic code-only release; never install or restart it."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import subprocess
import tarfile

MANIFEST = "RELEASE-MANIFEST.json"
PACKAGE_DATA = {"courswatch/conversation/prompt-haddock.txt"}
FIXTURE_ROOT = "tests/fixtures/media"
FIXTURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".json"}
TREE_SUFFIXES = {
    "courswatch": {".py"}, "ops": {".py"}, "tools": {".py"},
    "tests": {".py", ".md"}, "systemd": {".service", ".timer", ".path", ".conf"},
    "assets": {".png", ".ogg", ".json"}, "docs": {".md"},
}
FORBIDDEN = {".git", "__pycache__", ".env", "browser-profile", "models", "reports", ".venv"}


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.as_posix() != name or "\\" in name or path.is_absolute() or PureWindowsPath(name).drive
            or ".." in path.parts or any(part in FORBIDDEN for part in path.parts)
            or any(part.lower().startswith(("secrets", "cookies", "password")) for part in path.parts)):
        raise ValueError("Unsafe release path")
    return path


def allowed_source(name):
    path = safe_name(name)
    if name in PACKAGE_DATA:
        return True
    if path.is_relative_to(FIXTURE_ROOT) and path.suffix in FIXTURE_SUFFIXES:
        return True
    if len(path.parts) == 1:
        return (name == "main.py" or path.suffix == ".sh" or name == "README.md"
                or (name.startswith("requirements") and path.suffix in {".lock", ".txt"}))
    return path.parts[0] in TREE_SUFFIXES and path.suffix in TREE_SUFFIXES[path.parts[0]]


def source_files(root):
    """An extension/path allow-list, never a recursive copy of the checkout."""
    root = Path(root).resolve()
    selected = []
    for path in root.iterdir():
        if path.is_file() and (path.name == "main.py" or path.suffix == ".sh"
                               or path.name == "README.md"
                               or (path.name.startswith("requirements") and path.suffix in {".lock", ".txt"})):
            selected.append(path)
    for directory, suffixes in TREE_SUFFIXES.items():
        folder = root / directory
        if folder.is_symlink():
            raise ValueError("Symlinked release directory")
        if folder.exists():
            selected.extend(path for path in folder.rglob("*")
                            if path.is_file() and path.suffix in suffixes
                            and not any(part in FORBIDDEN for part in path.relative_to(root).parts))
    fixture_dir = root / FIXTURE_ROOT
    if fixture_dir.is_symlink():
        raise ValueError("Symlinked fixture directory")
    if fixture_dir.exists():
        selected.extend(path for path in fixture_dir.rglob("*")
                        if path.is_file() and path.suffix in FIXTURE_SUFFIXES)
    selected.extend(root / name for name in PACKAGE_DATA if (root / name).is_file())
    for path in sorted(set(selected)):
        name = path.relative_to(root).as_posix()
        if not allowed_source(name):
            raise ValueError("Unexpected release source")
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError("Symlinked release source")
        yield name, path


def git_metadata(root):
    def git(*args):
        return subprocess.check_output(["git", "--no-optional-locks", "-C", str(root), *args], text=True).strip()
    return {"revision": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def _member(archive, name, payload):
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o755 if name.endswith(".sh") else 0o644
    info.mtime = 0
    archive.addfile(info, io.BytesIO(payload))


def build_release(root, output, *, revision, dirty=False):
    root, output = Path(root).resolve(), Path(output)
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        raise ValueError("A full source commit identifier is required")
    payloads = {name: path.read_bytes() for name, path in source_files(root)}
    if not payloads:
        raise ValueError("Empty release")
    if any(len(data) > 16 * 1024 * 1024 for data in payloads.values()):
        raise ValueError("Unexpected release source size")
    if len({name.casefold() for name in payloads}) != len(payloads):
        raise ValueError("Case-colliding release paths")
    manifest = {
        "schema_version": 1, "revision": revision, "dirty": bool(dirty),
        "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in payloads.items()],
        "dependencies": {name: data.decode("utf-8") for name, data in payloads.items()
                         if "/" not in name and name.startswith("requirements")},
    }
    # The archive and hashes use the same in-memory byte snapshot.
    with output.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name, data in payloads.items():
                    _member(archive, name, data)
                _member(archive, MANIFEST, (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    return manifest


def verify_release(path):
    payloads = {}
    folded = set()
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            safe_name(member.name)
            if not member.isfile() or member.name.casefold() in folded or member.size > 16 * 1024 * 1024:
                raise ValueError("Invalid or duplicate release member")
            folded.add(member.name.casefold())
            with archive.extractfile(member) as stream:
                payloads[member.name] = stream.read()
    manifest = json.loads(payloads.pop(MANIFEST))
    if (manifest.get("schema_version") != 1
            or not re.fullmatch(r"[a-f0-9]{40,64}", manifest.get("revision", ""))
            or not isinstance(manifest.get("dirty"), bool)):
        raise ValueError("Unsupported release manifest")
    expected = {}
    for entry in manifest["files"]:
        if not allowed_source(entry["path"]):
            raise ValueError("Unexpected release member")
        if entry["path"] in expected:
            raise ValueError("Duplicate manifest entry")
        expected[entry["path"]] = entry
    if payloads.keys() != expected.keys():
        raise ValueError("Release member inventory mismatch")
    for name, data in payloads.items():
        entry = expected[name]
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Release member hash mismatch: " + name)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    if args.verify:
        manifest = verify_release(args.verify)
    else:
        if not args.output:
            parser.error("--output or --verify is required")
        metadata = git_metadata(args.root)
        if metadata["dirty"] and not args.allow_dirty:
            parser.error("Checkout has local changes; review them or explicitly use --allow-dirty")
        manifest = build_release(args.root, args.output, **metadata)
        verify_release(args.output)
    print(json.dumps({"revision": manifest["revision"], "dirty": manifest["dirty"], "files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
