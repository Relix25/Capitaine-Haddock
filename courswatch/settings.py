"""Shared runtime paths. Defaults preserve the existing Linux installation."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    data: Path
    config: Path
    secrets: Path
    python: str
    collector: str

    @classmethod
    def from_environment(cls):
        return cls(Path(os.environ.get('COURS_WATCH_DATA', '/var/lib/cours-watch')),
                   Path(os.environ.get('COURS_WATCH_CONFIG', '/etc/cours-watch/config.json')),
                   Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json')),
                   os.environ.get('COURS_WATCH_PYTHON', '/opt/cours-watch/venv/bin/python'),
                   os.environ.get('COURS_WATCH_COLLECTOR', '/opt/cours-watch/cours_watch.py'))


def scan_command(data, config):
    paths = RuntimePaths.from_environment()
    return [paths.python, paths.collector, 'scan', '--config', str(config), '--data-dir', str(data)]
