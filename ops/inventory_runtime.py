"""Read-only VPS inventory: selected code hashes and non-secret unit properties."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess

UNITS = ("cours-watch.service", "cours-watch-bot.service", "cours-watch-esn.service",
         "cours-watch-session.service", "cours-watch-obsidian.service")
PROPERTIES = ("Id", "ActiveState", "SubState", "User", "Group", "WorkingDirectory",
              "FragmentPath", "DropInPaths", "ReadWritePaths", "NoNewPrivileges", "ProtectSystem")


def inventory(root=Path("/opt/cours-watch"), runner=subprocess.run):
    root = Path(root)
    code = {}
    candidates = list(root.glob("*.py"))
    for name in ("courswatch", "ops"):
        folder = root / name
        if folder.exists() and not folder.is_symlink():
            candidates.extend(folder.rglob("*.py"))
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if any(part in {"venv", "bot-venv", "translation-venv", "__pycache__", "models", "reports"}
               for part in relative.parts) or path.is_symlink():
            continue
        code[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    units = {}
    for unit in UNITS:
        result = runner(["systemctl", "show", unit, "--no-pager", "--property=" + ",".join(PROPERTIES)],
                        capture_output=True, text=True, timeout=10, check=False)
        units[unit] = {"returncode": result.returncode,
                       "properties": {key: value for line in result.stdout.splitlines() if "=" in line
                                      for key, value in [line.split("=", 1)] if key in PROPERTIES}}
    environments = {}
    for name in ("venv", "bot-venv", "translation-venv"):
        executable = root / name / "bin/python"
        if executable.is_file():
            result = runner([str(executable), "-B", "-I", "-c",
                             "import importlib.metadata,json,platform;print(json.dumps({'python':platform.python_version(),'packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()}}))"],
                            capture_output=True, text=True, timeout=20, check=False)
            environments[name] = json.loads(result.stdout) if result.returncode == 0 else {"error": "inventory_failed"}
    return {"host_python": platform.python_version(), "code_sha256": code,
            "units": units, "environments": environments}


def main():
    print(json.dumps(inventory(), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
