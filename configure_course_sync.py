#!/usr/bin/env python3
"""Add the dedicated obsidian-cours Syncthing share to an existing XML config.

Dry-run is the default. This script intentionally does not call Syncthing's API,
create .stignore, or touch files in the vault.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


FOLDER_ID = "obsidian-cours"
FOLDER_LABEL = "Obsidian Cours"
SERVER_PATH = "/srv/obsidian/cours"
PC_PATH = r"C:\Users\basti\Documents\ChatGPT\Cours"
SERVER_ID_DEFAULT = "NXLAOUH-M3TZAVT-T5L6ZX4-U46AIY4-2JTGH2E-GLVDAJH-PR4MDMN-FN3HIAN"
PC_ID_DEFAULT = "PFUYAJJ-UBDGI6D-PVT54BH-7YEQBUO-PPETOFB-HXOK4ID-5S2HIYI-VGBQVQX"
OLD_SERVER_ID = "6ATRTBV"


class ConfigError(Exception):
    """Safe-to-display validation error; never includes raw XML values."""


def _normalize_path(value: str) -> str:
    value = value.strip()
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return value.replace("/", "\\").rstrip("\\").casefold()
    return os.path.normpath(value)


def _id_text(element: ET.Element) -> str:
    return element.get("id", "").strip()


def _find_template(root: ET.Element, name: str) -> ET.Element:
    defaults = root.find("defaults")
    template = defaults.find(name) if defaults is not None else None
    if template is None:
        raise ConfigError(f"Missing <defaults><{name}> template; refusing to guess schema defaults.")
    return template


def _get_containers(root: ET.Element) -> tuple[ET.Element, ET.Element]:
    # Syncthing stores <folder> and <device> elements directly below
    # <configuration>; it does not wrap them in plural containers.
    return root, root


def _set_child_text(parent: ET.Element, tag: str, value: str) -> None:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = value


def _set_folder_settings(folder: ET.Element, *, role: str, vault: str, remote_id: str) -> None:
    folder.set("id", FOLDER_ID)
    folder.set("label", FOLDER_LABEL)
    folder.set("path", vault)
    folder.set("type", "sendonly" if role == "server" else "receiveonly")
    folder.set("ignorePerms", "true")
    folder.set("fsWatcherEnabled", "true")
    folder.set("rescanIntervalS", "300")
    folder.set("paused", "false")

    # Never inherit sharing peers from a template. This folder has exactly one
    # remote peer; the local Syncthing instance is implicit.
    for child in list(folder):
        if child.tag == "device":
            folder.remove(child)
    ET.SubElement(folder, "device", {"id": remote_id})


def _target_folder(folders: ET.Element) -> ET.Element | None:
    matches = [item for item in folders.findall("folder") if _id_text(item) == FOLDER_ID]
    if len(matches) > 1:
        raise ConfigError(f"More than one folder uses id {FOLDER_ID}; refusing ambiguous edit.")
    return matches[0] if matches else None


def _ensure_folder(root: ET.Element, *, role: str, vault: str, remote_id: str) -> str:
    folders, _ = _get_containers(root)
    existing = _target_folder(folders)
    expected_type = "sendonly" if role == "server" else "receiveonly"
    if existing is not None:
        if _normalize_path(existing.get("path", "")) != _normalize_path(vault):
            raise ConfigError(f"Folder id {FOLDER_ID} exists at an unexpected path; refusing overwrite.")
        if existing.get("type", "") != expected_type:
            raise ConfigError(f"Folder id {FOLDER_ID} has an unexpected type; refusing overwrite.")
        folder = existing
        action = "update existing dedicated folder"
    else:
        folder = copy.deepcopy(_find_template(root, "folder"))
        folders.append(folder)
        action = "add dedicated folder from Syncthing defaults template"
    _set_folder_settings(folder, role=role, vault=vault, remote_id=remote_id)
    return action


def _device_for_id(devices: ET.Element, device_id: str) -> ET.Element | None:
    matches = [item for item in devices.findall("device") if _id_text(item) == device_id]
    if len(matches) > 1:
        raise ConfigError("More than one device uses the requested peer ID; refusing ambiguous edit.")
    return matches[0] if matches else None


def _ensure_peer(root: ET.Element, *, peer_id: str, name: str, address: str) -> str:
    _, devices = _get_containers(root)
    peer = _device_for_id(devices, peer_id)
    if peer is None:
        peer = copy.deepcopy(_find_template(root, "device"))
        peer.set("id", peer_id)
        devices.append(peer)
        action = "add peer from Syncthing defaults template"
        peer.set("name", name)
    else:
        action = "update address for existing peer"
    # Addresses belong to the peer ID. Replace only the address list; preserve
    # compression, introducer, cert-name and any other device settings.
    addresses = peer.findall("address")
    if addresses:
        addresses[0].text = address
        for extra in addresses[1:]:
            peer.remove(extra)
    else:
        _set_child_text(peer, "address", address)
    return action


def _ensure_pc_listener(root: ET.Element) -> list[str]:
    options = root.find("options")
    if options is None:
        raise ConfigError("PC config has no <options> element; refusing to invent a partial config.")
    listen = options.findall("listenAddress")
    if listen:
        listen[0].text = "tcp://10.77.77.2:22000"
        for extra in listen[1:]:
            options.remove(extra)
    else:
        _set_child_text(options, "listenAddress", "tcp://10.77.77.2:22000")
    for tag in ("globalAnnounceEnabled", "localAnnounceEnabled", "natEnabled", "relaysEnabled"):
        _set_child_text(options, tag, "false")
    return [
        "set PC listen address to tcp://10.77.77.2:22000",
        "disable global discovery, local discovery, NAT traversal and relays on PC",
    ]


def _select_role(args: argparse.Namespace) -> tuple[str, str, str, str]:
    if args.role == "server":
        expected_vault, peer_id, peer_name, address = SERVER_PATH, args.pc_id, "Cours PC", "tcp://10.77.77.2:22000"
    else:
        expected_vault, peer_id, peer_name, address = PC_PATH, args.server_id, "Cours VPS", "tcp://10.77.77.1:22000"
    if _normalize_path(args.vault) != _normalize_path(expected_vault):
        raise ConfigError(f"Vault path for role {args.role} must be the configured course vault.")
    return args.vault, peer_id, peer_name, address


def build_plan(xml_bytes: bytes, args: argparse.Namespace) -> tuple[bytes, dict[str, object]]:
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(xml_bytes, parser=parser)
    except ET.ParseError as exc:
        raise ConfigError(f"Invalid Syncthing XML (line {exc.position[0]}, column {exc.position[1]}).") from None
    if root.tag != "configuration":
        raise ConfigError("Expected a Syncthing <configuration> XML root.")
    vault, remote_id, peer_name, peer_address = _select_role(args)
    folder_action = _ensure_folder(root, role=args.role, vault=vault, remote_id=remote_id)
    peer_action = _ensure_peer(root, peer_id=remote_id, name=peer_name, address=peer_address)
    operations = [folder_action, peer_action]
    if args.role == "pc":
        operations.extend(_ensure_pc_listener(root))
    rendered = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    public_plan: dict[str, object] = {
        "role": args.role,
        "folder_id": FOLDER_ID,
        "folder_label": FOLDER_LABEL,
        "vault_path": vault,
        "folder_type": "sendonly" if args.role == "server" else "receiveonly",
        "peer_id": remote_id,
        "peer_address": peer_address,
        "operations": operations,
        "apply": bool(args.apply),
    }
    return rendered, public_plan


def _private_backup(config_path: Path, contents: bytes) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = config_path.with_name(f"{config_path.name}.{stamp}.bak")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = config_path.with_name(f"{config_path.name}.{stamp}.{suffix}.bak")
        suffix += 1
    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == "nt":
            # Remove inherited access and grant the current account full control.
            # Capture output so account names or system diagnostics never reach stdout.
            result = subprocess.run(
                ["icacls", str(candidate), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:(F)"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0:
                raise ConfigError("Could not restrict backup ACL to the current Windows account.")
        else:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as backup:
            fd = -1
            backup.write(contents)
            backup.flush()
            os.fsync(backup.fileno())
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            candidate.unlink()
        except OSError:
            pass
        raise
    return candidate


def apply_plan(config_path: Path, original: bytes, rendered: bytes) -> None:
    original_hash = hashlib.sha256(original).digest()
    _private_backup(config_path, original)
    current = config_path.read_bytes()
    if hashlib.sha256(current).digest() != original_hash:
        raise ConfigError("Config changed since it was read; backup retained and update aborted.")

    mode = config_path.stat().st_mode
    fd, tmp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        # Recheck immediately before the atomic replace to close the edit race.
        if hashlib.sha256(config_path.read_bytes()).digest() != original_hash:
            raise ConfigError("Config changed during update; backup retained and update aborted.")
        os.replace(tmp_path, config_path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("pc", "server"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=str)
    parser.add_argument("--pc-id", required=True)
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Back up privately and atomically write the config.")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config_path = args.config.expanduser().resolve(strict=True)
        if not config_path.is_file():
            raise ConfigError("Config path is not a regular file.")
        original = config_path.read_bytes()
        rendered, plan = build_plan(original, args)
        if args.apply:
            apply_plan(config_path, original, rendered)
            plan["status"] = "applied"
        else:
            plan["status"] = "dry-run"
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError:
        # Avoid printing exception details that could contain paths or file data.
        print("error: unable to read or write the requested config file", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
