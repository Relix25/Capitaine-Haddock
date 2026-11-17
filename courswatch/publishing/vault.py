"""Publish the pedagogical handoff and immutable PDF history into the master vault."""
import argparse
import json
from pathlib import Path

from courswatch.publishing.obsidian import update_vault
from courswatch.documents.publish import publish_pdf_versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot')
    parser.add_argument('documents')
    parser.add_argument('vault')
    args = parser.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    notes = update_vault(snapshot, args.vault)
    pdfs = publish_pdf_versions(args.documents, args.vault)
    print(json.dumps({'snapshot_checked_utc': snapshot['checked_utc'],
                      'automatic_notes': len(notes), 'documents': pdfs}, ensure_ascii=False))


if __name__ == '__main__':
    main()
