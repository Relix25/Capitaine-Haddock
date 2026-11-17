"""Shared runtime paths and explicit argv for package entry points."""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    data: Path
    config: Path
    secrets: Path
    python: str
    collector_argv: tuple[str, ...]

    @classmethod
    def from_environment(cls):
        # An explicit historical override is still a script path, never shell text.
        override = os.environ.get('COURS_WATCH_COLLECTOR')
        return cls(Path(os.environ.get('COURS_WATCH_DATA', '/var/lib/cours-watch')),
                   Path(os.environ.get('COURS_WATCH_CONFIG', '/etc/cours-watch/config.json')),
                   Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json')),
                   os.environ.get('COURS_WATCH_PYTHON', '/opt/cours-watch/venv/bin/python'),
                   (override,) if override else ('-m', 'courswatch'))


def scan_command(data, config):
    paths = RuntimePaths.from_environment()
    return [paths.python, *paths.collector_argv, 'scan', '--config', str(config), '--data-dir', str(data)]
