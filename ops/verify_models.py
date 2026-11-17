"""Verify local translation archives against an explicitly reviewed manifest; no download."""
import argparse
import hashlib
import json
from pathlib import Path
import re

PAIRS = {"en_fr", "ro_en"}


def verify_models(manifest_path, archive_dir):
    expected = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if set(expected) != PAIRS:
        raise ValueError("Expected exactly the reviewed en_fr and ro_en models")
    verified = {}
    for pair in sorted(PAIRS):
        item = expected[pair]
        if (not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", ""))
                or not item.get("version") or not item.get("source", "").startswith("https://argos-net.com/v1/")):
            raise ValueError("Missing reviewed model identity")
        path = Path(archive_dir) / (pair + ".argosmodel")
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Model archive hash mismatch: " + pair)
        verified[pair] = item["sha256"]
    return verified


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps({"verified": verify_models(args.manifest, args.archives)}, sort_keys=True))


if __name__ == "__main__":
    main()
