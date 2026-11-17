"""Prepare a legacy-compatible COPY of the current quota ledger; never install it.

Keep every charged/reserved call, notice and blocked day. Only the additive v2
schema marker is removed on the output copy; its provenance table is retained.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def _copy_path(value):
    path = Path(value)
    if path.is_symlink():
        raise ValueError('Use a real local copy, not a symlink')
    path = path.resolve()
    protected = ('/etc/cours-watch', '/var/lib/cours-watch', '/var/lib/cours-watch-publish', '/srv/obsidian')
    if any(path == Path(root) or Path(root) in path.parents for root in protected):
        raise ValueError('Only local copies are permitted')
    return path


def prepare_copy(source_copy, output_copy):
    source, target = _copy_path(source_copy), _copy_path(output_copy)
    if source == target:
        raise ValueError('Source and output must be distinct copies')
    if not source.is_file():
        raise FileNotFoundError(source)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as origin:
        versions = {row[0] for row in origin.execute(
            "SELECT version FROM schema_migrations WHERE component='budget'")}
        if 1 not in versions or versions - {1, 2}:
            raise ValueError('Only reviewed budget v1/v2 copies are supported')
        # Exclusive creation: never replace an existing ledger or rollback file.
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(target)) as result:
                origin.backup(result)
                result.execute('BEGIN IMMEDIATE')
                result.execute("DELETE FROM schema_migrations WHERE component='budget' AND version=2")
                result.commit()
                counts = {name: result.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                          for name in ('calls', 'notices', 'blocked_days')}
                if result.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Invalid output copy')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return {'schema':'budget-v1-compatible-copy', **counts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-copy', type=Path, required=True)
    parser.add_argument('--output-copy', type=Path, required=True)
    args = parser.parse_args(argv)
    print(prepare_copy(args.source_copy, args.output_copy))


if __name__ == '__main__':
    main()
