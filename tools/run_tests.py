"""Run explicit, offline test profiles with disposable fixture directories."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def course_vault():
    """Identify this Windows vault without treating an arbitrary staging parent as one."""
    return next((parent for parent in ROOT.parents
                 if parent.name.casefold() == 'cours' and (parent / 'Outils').is_dir()), None)


PROFILES = {
    "core": (
        "test_course_tasks", "test_course_bot", "test_reminders",
        "test_discord_presentation", "test_course_health", "test_state",
    ),
    "conversation": (
        "test_luna_brain", "test_luna_chat", "test_esn_watch",
        "test_haddock_voice", "test_haddock_emojis",
        "test_haddock_easter_egg", "test_course_translation", "tests.unit.test_conversation_metrics", "tests.unit.test_model_switch",
    ),
    "documents": (
        "test_moodle_pdf_versions", "test_moodle_pdf_collection", "test_attachment_bindings",
        "test_course_pdf_publish", "test_course_obsidian", "test_course_capture",
    ),
    "migration": (
        "test_configure_course_sync", "test_prepare_course_seed",
        "test_apply_course_seed_pc",
    ),
    "regression": (
        "test_pipeline", "test_schema", "test_compatibility", "test_refresh", "tests.unit.test_responses",
    ),
    "operations": (
        "test_operations", "tests.operations.test_channel_validation",
    ),
}


def _within(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _normalized(path: str | os.PathLike) -> str:
    return os.path.abspath(os.fsdecode(path)).replace("\\", "/").casefold()


def install_safety() -> None:
    """Reject external sockets and access to real service/vault secrets.

    Loopback sockets are required by asyncio's Windows event loop. This is a
    test guard, not a general-purpose sandbox for untrusted Python programs.
    """
    allowed = [_normalized(ROOT)]
    allowed.extend(_normalized(value) for value in json.loads(
        os.environ.get("COURSWATCH_TEST_READ_PATHS", "[]")
    ))
    vault_root = course_vault()
    vault = _normalized(vault_root) if vault_root is not None else None
    protected = [_normalized(path) for path in (
        "/etc/cours-watch", "/var/lib/cours-watch", "/var/lib/cours-watch-publish",
        "/srv/obsidian/cours", "/srv/obsidian/vault", Path.home() / ".ssh",
    )]

    def loopback(address):
        try:
            return isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return False

    # A raw socket may resolve its hostname in C before emitting the connect
    # audit event. Reject it before calling the original method as well.
    for method in ("connect", "connect_ex", "bind"):
        original = getattr(socket.socket, method)

        def checked(self, address, _original=original):
            if not loopback(address):
                raise RuntimeError("External network disabled by the offline test runner")
            return _original(self, address)

        setattr(socket.socket, method, checked)
    original_sendto = socket.socket.sendto

    def checked_sendto(self, data, *args):
        if not args or not loopback(args[-1]):
            raise RuntimeError("External network disabled by the offline test runner")
        return original_sendto(self, data, *args)

    socket.socket.sendto = checked_sendto

    def guard(event, args):
        if event in {"socket.connect", "socket.bind", "socket.sendto"}:
            address = args[1]
            if not loopback(address):
                raise RuntimeError("External network disabled by the offline test runner")
        elif event == "socket.getaddrinfo":
            try:
                local = ipaddress.ip_address(args[0]).is_loopback
            except (ValueError, TypeError):
                local = args[0] == "localhost"
            if not local:
                raise RuntimeError("External DNS disabled by the offline test runner")
        elif event in {"open", "os.listdir", "os.scandir"}:
            value = args[0]
            if not isinstance(value, (str, bytes, os.PathLike)):
                return
            path = _normalized(value)
            if any(_within(path, directory) for directory in protected):
                raise RuntimeError("Production state is unavailable to unit tests")
            if vault is not None and _within(path, vault) and not any(_within(path, directory) for directory in allowed):
                raise RuntimeError("The course vault is unavailable to unit tests")

    sys.addaudithook(guard)


def _environment(directory: Path, dependency_paths: list[Path]) -> None:
    for name in tuple(os.environ):
        if name.startswith(("COURS_WATCH_", "COURSWATCH_TEST_")) or name in {
            "OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "CMD_API_KEY", "COMMANDCODE_API_KEY",
            "DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_URL",
        }:
            os.environ.pop(name, None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(directory)
    tempfile.tempdir = str(directory)
    for name, leaf in {
        "COURS_WATCH_CONFIG": "absent-config.json",
        "COURS_WATCH_SECRETS": "absent-secrets.json",
        "COURS_WATCH_DATA": "synthetic-data",
        "COURS_WATCH_EMOJIS": "absent-emojis.json",
        "COURS_WATCH_TRANSLATION_CONFIG": "absent-translation.json",
    }.items():
        os.environ[name] = str(directory / leaf)
    os.environ["COURSWATCH_TEST_READ_PATHS"] = json.dumps([str(path) for path in dependency_paths])
    # Fixture CLIs use the same guard. Their synthetic data remain disposable.
    guard_dir = directory / "python-guard"
    guard_dir.mkdir()
    (guard_dir / "sitecustomize.py").write_text(
        "import runpy\n"
        f"runpy.run_path({str(Path(__file__).resolve())!r}, "
        "run_name='_courswatch_test_guard')['install_safety']()\n",
        encoding="utf-8",
    )
    os.environ["PYTHONPATH"] = os.pathsep.join(
        str(path) for path in [guard_dir, ROOT, *dependency_paths]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=[*PROFILES, "all"], nargs="?", default="core")
    parser.add_argument("--list", action="store_true", help="List modules without importing them")
    parser.add_argument("--temp-root", type=Path, default=ROOT / ".test-tmp")
    parser.add_argument("--site-packages", action="append", type=Path, default=[],
                        help="Use an existing dependency directory without installing anything")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    names = list(dict.fromkeys(
        name for profile in (PROFILES.values() if args.profile == "all" else [PROFILES[args.profile]])
        for name in profile
    ))
    if args.list:
        print("\n".join(names))
        return 0
    paths = [path.resolve(strict=True) for path in args.site_packages]
    for path in reversed(paths):
        sys.path.insert(0, str(path))
    sys.path.insert(0, str(ROOT))
    base = args.temp_root.resolve()
    base_name = _normalized(base)
    vault_root = course_vault()
    vault = _normalized(vault_root) if vault_root is not None else None
    if vault is not None and _within(base_name, vault) and not _within(base_name, _normalized(ROOT)):
        parser.error("--temp-root must be outside the course vault or inside the bot repository")
    for directory in ("/etc/cours-watch", "/var/lib/cours-watch",
                      "/var/lib/cours-watch-publish", "/srv/obsidian"):
        if _within(base_name, _normalized(directory)):
            parser.error("--temp-root cannot point at production state")
    base.mkdir(parents=True, exist_ok=True)
    # Cleanup also runs when importing or setting up a test fails.
    with tempfile.TemporaryDirectory(prefix="courswatch-tests-", dir=base) as temporary:
        _environment(Path(temporary), paths)
        install_safety()
        suite = unittest.TestLoader().loadTestsFromNames(names)
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
        print(json.dumps({"profile": args.profile, "tests": result.testsRun,
                          "failures": len(result.failures), "errors": len(result.errors),
                          "skipped": len(result.skipped)}, ensure_ascii=False))
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
