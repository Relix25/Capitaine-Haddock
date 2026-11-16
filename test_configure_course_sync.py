import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from unittest import mock

import configure_course_sync as sync


SERVER = sync.SERVER_ID_DEFAULT
PC = sync.PC_ID_DEFAULT


def fixture_xml() -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<configuration version="52">
  <!-- Keep comments and all existing device/folder state. -->
  <folder id="obsidian-vps" label="Existing share" path="/srv/obsidian/ancien" type="sendonly" ignorePerms="false">
    <device id="{sync.OLD_SERVER_ID}" />
    <rescanIntervalS>900</rescanIntervalS>
  </folder>
  <device id="{sync.OLD_SERVER_ID}"><name>Old peer</name><address>dynamic</address><compression>metadata</compression></device>
  <gui enabled="true"><address>127.0.0.1:8384</address><apikey>SECRET_API_KEY</apikey><tlsCert>SECRET_CERTIFICATE</tlsCert></gui>
  <options>
    <listenAddress>default</listenAddress><listenAddress>tcp://127.0.0.1:22000</listenAddress>
    <globalAnnounceEnabled>true</globalAnnounceEnabled><localAnnounceEnabled>true</localAnnounceEnabled>
    <natEnabled>true</natEnabled><relaysEnabled>true</relaysEnabled><maxSendKbps>0</maxSendKbps>
  </options>
  <defaults>
    <folder id="" label="" path="" type="sendreceive" rescanIntervalS="3600" fsWatcherEnabled="false" fsWatcherDelayS="10s" ignorePerms="false" paused="false" customOption="preserve-me">
      <filesystemType>basic</filesystemType><versioning type=""><cleanupIntervalS>3600</cleanupIntervalS></versioning>
      <minDiskFree unit="%">1</minDiskFree><markerName>.stfolder</markerName>
    </folder>
    <device id=""><name></name><address>dynamic</address><compression>metadata</compression><introducer>false</introducer><paused>false</paused></device>
  </defaults>
</configuration>'''.encode()


def args_for(role: str, *, apply: bool = False) -> sync.argparse.Namespace:
    return sync.argparse.Namespace(
        role=role,
        config=Path("unused.xml"),
        vault=sync.SERVER_PATH if role == "server" else sync.PC_PATH,
        pc_id=PC,
        server_id=SERVER,
        apply=apply,
    )


class ConfigureCourseSyncTests(unittest.TestCase):
    def parsed(self, data: bytes) -> ET.Element:
        return ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))

    def test_pc_plan_is_secret_free_and_preserves_old_share(self):
        original = fixture_xml()
        rendered, plan = sync.build_plan(original, args_for("pc"))
        self.assertEqual(original, fixture_xml())
        self.assertNotIn(b"SECRET_API_KEY", json.dumps(plan).encode())
        self.assertNotIn(b"SECRET_CERTIFICATE", json.dumps(plan).encode())
        root = self.parsed(rendered)
        folder = next(item for item in root.findall("folder") if item.get("id") == sync.FOLDER_ID)
        self.assertEqual(sync.PC_PATH, folder.get("path"))
        self.assertEqual("receiveonly", folder.get("type"))
        self.assertEqual("true", folder.get("ignorePerms"))
        self.assertEqual("true", folder.get("fsWatcherEnabled"))
        self.assertEqual("300", folder.get("rescanIntervalS"))
        self.assertEqual("false", folder.get("paused"))
        self.assertEqual([SERVER], [d.get("id") for d in folder.findall("device")])
        self.assertEqual("preserve-me", folder.get("customOption"))
        existing = next(item for item in root.findall("folder") if item.get("id") == "obsidian-vps")
        self.assertEqual("/srv/obsidian/ancien", existing.get("path"))
        self.assertEqual([sync.OLD_SERVER_ID], [d.get("id") for d in existing.findall("device")])
        peer = next(item for item in root.findall("device") if item.get("id") == SERVER)
        self.assertEqual("tcp://10.77.77.1:22000", peer.findtext("address"))
        options = root.find("options")
        self.assertEqual(["tcp://10.77.77.2:22000"], [x.text for x in options.findall("listenAddress")])
        for field in ("globalAnnounceEnabled", "localAnnounceEnabled", "natEnabled", "relaysEnabled"):
            self.assertEqual("false", options.findtext(field))
        self.assertEqual("0", options.findtext("maxSendKbps"))
        self.assertEqual("SECRET_API_KEY", root.findtext("gui/apikey"))
        self.assertEqual("SECRET_CERTIFICATE", root.findtext("gui/tlsCert"))
        self.assertIn(b"Keep comments", rendered)

    def test_server_adds_only_pc_to_course_share_and_keeps_old_peer(self):
        rendered, _ = sync.build_plan(fixture_xml(), args_for("server"))
        root = self.parsed(rendered)
        folder = next(item for item in root.findall("folder") if item.get("id") == sync.FOLDER_ID)
        self.assertEqual(sync.SERVER_PATH, folder.get("path"))
        self.assertEqual("sendonly", folder.get("type"))
        self.assertEqual([PC], [d.get("id") for d in folder.findall("device")])
        self.assertEqual("tcp://10.77.77.2:22000", next(item for item in root.findall("device") if item.get("id") == PC).findtext("address"))
        self.assertIsNotNone(next((item for item in root.findall("device") if item.get("id") == sync.OLD_SERVER_ID), None))
        self.assertEqual("127.0.0.1:8384", root.findtext("gui/address"))
        self.assertEqual("true", root.findtext("options/natEnabled"))

    def test_second_run_is_structurally_and_bytewise_idempotent(self):
        once, _ = sync.build_plan(fixture_xml(), args_for("pc"))
        twice, plan = sync.build_plan(once, args_for("pc"))
        self.assertEqual(once, twice)
        root = self.parsed(twice)
        self.assertEqual(1, len([x for x in root.findall("folder") if x.get("id") == sync.FOLDER_ID]))
        self.assertEqual(1, len([x for x in root.findall("device") if x.get("id") == SERVER]))
        self.assertEqual("update existing dedicated folder", plan["operations"][0])

    def test_cli_dry_run_does_not_write_config_or_print_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.xml"
            original = fixture_xml()
            config.write_bytes(original)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = sync.main([
                    "--role", "pc", "--config", str(config), "--vault", sync.PC_PATH,
                    "--pc-id", PC, "--server-id", SERVER,
                ])
            self.assertEqual(0, code)
            self.assertEqual(original, config.read_bytes())
            self.assertEqual("", stderr.getvalue())
            output = stdout.getvalue()
            self.assertIn('"status": "dry-run"', output)
            self.assertNotIn("SECRET_API_KEY", output)
            self.assertNotIn("SECRET_CERTIFICATE", output)

    def test_hash_guard_aborts_if_config_changes_after_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.xml"
            original = fixture_xml()
            config.write_bytes(original)
            changed = original + b"\n<!-- concurrent edit -->\n"

            def backup_then_change(path, contents):
                backup = Path(directory) / "private.bak"
                backup.write_bytes(contents)
                path.write_bytes(changed)
                return backup

            rendered, _ = sync.build_plan(original, args_for("pc"))
            with mock.patch.object(sync, "_private_backup", side_effect=backup_then_change):
                with self.assertRaisesRegex(sync.ConfigError, "changed since it was read"):
                    sync.apply_plan(config, original, rendered)
            self.assertEqual(changed, config.read_bytes())
            self.assertEqual(original, (Path(directory) / "private.bak").read_bytes())

    def test_conflicting_folder_id_refuses_update(self):
        root = self.parsed(fixture_xml())
        conflict = ET.SubElement(root, "folder", {"id": sync.FOLDER_ID, "path": "C:/unexpected", "type": "receiveonly"})
        with self.assertRaisesRegex(sync.ConfigError, "unexpected path"):
            sync.build_plan(ET.tostring(root), args_for("pc"))

    def test_apply_makes_private_backup_then_atomic_idempotent_write(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.xml"
            original = fixture_xml()
            config.write_bytes(original)
            args = args_for("pc", apply=True)
            rendered, _ = sync.build_plan(original, args)
            sync.apply_plan(config, original, rendered)
            backups = list(Path(directory).glob("config.xml.*.bak"))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_bytes())
            after_first = config.read_bytes()
            next_rendered, _ = sync.build_plan(after_first, args)
            sync.apply_plan(config, after_first, next_rendered)
            self.assertEqual(after_first, config.read_bytes())
            self.assertEqual(2, len(list(Path(directory).glob("config.xml.*.bak"))))

    def test_role_rejects_unexpected_vault_path(self):
        args = args_for("server")
        args.vault = "/srv/obsidian/other"
        with self.assertRaisesRegex(sync.ConfigError, "must be the configured course vault"):
            sync.build_plan(fixture_xml(), args)


if __name__ == "__main__":
    unittest.main()
