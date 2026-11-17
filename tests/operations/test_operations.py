"""Synthetic offline checks for release preparation and import-safe operations."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.build_release import MANIFEST, build_release, verify_release
from ops.inventory_runtime import inventory, PROPERTIES
from ops.verify_models import verify_models
from prepare_bot_repository import verified_export
import export_bot_source

ROOT = Path(__file__).resolve().parents[2]


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="haddock-ops-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, data=b"# code\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def bundle(self, path, members):
        with tarfile.open(path, "w") as archive:
            for name, data in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_historical_operations_import_without_network_or_writes(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("Import attempted an operational side effect")
        with patch("urllib.request.urlopen", forbidden), patch("subprocess.run", forbidden), \
                patch.object(Path, "mkdir", forbidden), patch.object(Path, "write_text", forbidden), \
                patch.object(Path, "write_bytes", forbidden), patch("sqlite3.connect", forbidden):
            for name in ("install_translation_models", "install_reminders", "verify_bot_live"):
                spec = importlib.util.spec_from_file_location("audit_" + name, ROOT / (name + ".py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertTrue(callable(module.main))

    def test_release_is_deterministic_and_excludes_private_state(self):
        self.write("course_bot.py")
        self.write("requirements-bot.txt", b"discord.py==2.7.1\n")
        self.write("courswatch/runtime.py")
        self.write("systemd/cours-watch.service", b"[Service]\n")
        self.write("assets/haddock-audio/manifest.json", b"{}")
        for name in (".env", "secrets.json", "state.db", "config.json", "reports/latest.json", "models/weights.bin"):
            self.write(name, b"must not be exported")
        first, second = self.root / "first.tar.gz", self.root / "second.tar.gz"
        build_release(self.root, first, revision="a" * 40)
        build_release(self.root, second, revision="a" * 40)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        manifest = verify_release(first)
        self.assertEqual(len(manifest["files"]), 5)
        self.assertEqual(manifest["dependencies"]["requirements-bot.txt"], "discord.py==2.7.1\n")
        self.assertFalse(manifest["dirty"])

    def test_release_never_overwrites_existing_output(self):
        self.write("course_bot.py")
        target = self.write("release.tar.gz", b"keep")
        with self.assertRaises(FileExistsError):
            build_release(self.root, target, revision="a" * 40)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_release_carries_only_the_declared_character_prompt_data(self):
        prompt = "# Rôle du capitaine\nPrompt synthétique.".encode('utf-8')
        self.write("courswatch/conversation/prompt-haddock.txt", prompt)
        self.write("courswatch/conversation/private-note.txt", b"must not be exported")
        target = self.root / "release.tar.gz"
        manifest = build_release(self.root, target, revision="a" * 40)
        self.assertEqual([entry['path'] for entry in manifest['files']],
                         ["courswatch/conversation/prompt-haddock.txt"])
        with tarfile.open(target, "r:gz") as archive:
            self.assertEqual(archive.extractfile("courswatch/conversation/prompt-haddock.txt").read(),prompt)
        verify_release(target)

    def test_release_rejects_tampered_member(self):
        self.write("course_bot.py", b"original")
        path = self.root / "release.tar.gz"
        manifest = build_release(self.root, path, revision="a" * 40)
        bad = self.root / "tampered.tar"
        self.bundle(bad, [("course_bot.py", b"changed"), (MANIFEST, json.dumps(manifest).encode())])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_release(bad)

    def test_release_rejects_traversal_links_duplicates_and_private_members(self):
        for name in ("../escape.py", "/absolute.py", "C:/escape.py", "a\\b.py", "./code.py", "a//b.py", ".env", "assets/secrets.json"):
            with self.subTest(name=name):
                path = self.root / "bad.tar"
                self.bundle(path, [(name, b"x")])
                with self.assertRaises(ValueError):
                    verify_release(path)
        path = self.root / "duplicate.tar"
        self.bundle(path, [("code.py", b"x"), ("code.py", b"x")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify_release(path)
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("link.py")
            info.type = tarfile.SYMTYPE
            info.linkname = "code.py"
            archive.addfile(info)
        with self.assertRaises(ValueError):
            verify_release(path)

    def test_source_symlink_is_refused(self):
        source = self.write("original.py")
        try:
            (self.root / "linked.py").symlink_to(source)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            build_release(self.root, self.root / "release.tar.gz", revision="a" * 40)

    def test_export_verifies_all_bytes_before_extraction(self):
        data = b"original"
        archive = self.root / "export.tar"
        manifest = self.write("manifest.json", json.dumps([
            {"path": "code.py", "sha256": hashlib.sha256(data).hexdigest()}]).encode())
        self.bundle(archive, [("code.py", data)])
        self.assertEqual(verified_export(archive, manifest), {"code.py": data})
        self.bundle(archive, [("code.py", b"changed")])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verified_export(archive, manifest)

    def test_export_archives_the_same_bytes_that_were_hashed(self):
        source = self.write("source/code.py", b"original")
        (self.root / "units").mkdir()
        paths = {
            "/opt/cours-watch": self.root / "source",
            "/etc/systemd/system": self.root / "units",
            "/tmp/capitaine-source-20261117.tar.gz": self.root / "export.tar.gz",
            "/tmp/capitaine-source-20261117-manifest.json": self.root / "manifest.json",
        }
        original_read = Path.read_bytes
        changed = False
        def read_then_change(path):
            nonlocal changed
            data = original_read(path)
            if path == source and not changed:
                changed = True
                source.write_bytes(b"changed after read")
            return data
        with patch.object(export_bot_source, "Path", side_effect=lambda p: paths.get(str(p), Path(p))), \
                patch.object(Path, "read_bytes", read_then_change), patch("builtins.print"):
            export_bot_source.main()
        self.assertEqual(source.read_bytes(), b"changed after read")
        self.assertEqual(verified_export(paths["/tmp/capitaine-source-20261117.tar.gz"],
                                         paths["/tmp/capitaine-source-20261117-manifest.json"]),
                         {"code.py": b"original"})

    def test_export_rejects_missing_extra_or_unsafe_members(self):
        manifest = self.write("manifest.json", b"[]")
        archive = self.root / "export.tar"
        for name in ("extra.py", "../escape.py", "C:/escape.py"):
            self.bundle(archive, [(name, b"x")])
            with self.assertRaises(ValueError):
                verified_export(archive, manifest)

    def test_models_require_reviewed_hashes_and_matching_bytes(self):
        expected = {}
        for pair in ("en_fr", "ro_en"):
            data = pair.encode()
            self.write(pair + ".argosmodel", data)
            expected[pair] = {"version": "1.9", "source": "https://argos-net.com/v1/model",
                              "sha256": hashlib.sha256(data).hexdigest()}
        manifest = self.write("manifest.json", json.dumps(expected).encode())
        self.assertEqual(set(verify_models(manifest, self.root)), {"en_fr", "ro_en"})
        self.write("en_fr.argosmodel", b"changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_models(manifest, self.root)
        expected["en_fr"]["sha256"] = ""
        manifest.write_text(json.dumps(expected))
        with self.assertRaisesRegex(ValueError, "identity"):
            verify_models(manifest, self.root)

    def test_inventory_never_requests_environment_or_reads_private_state(self):
        self.write("course_bot.py")
        self.write("courswatch/runtime.py")
        self.write("reports/private.py", b"private")
        self.write("bot-venv/lib/private.py", b"private")
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="User=courswatch\nEnvironment=SECRET\nActiveState=active\n")
        result = inventory(self.root, runner)
        self.assertEqual(set(result["code_sha256"]), {"course_bot.py", "courswatch/runtime.py"})
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertTrue(all("Environment" not in " ".join(call) for call in calls))
        self.assertNotIn("Environment", PROPERTIES)


if __name__ == "__main__":
    unittest.main()
