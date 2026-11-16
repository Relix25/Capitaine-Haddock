"""Inspect course content only; authentication files are never read."""
import argparse
import json
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument('course', choices=['AAIT','AITDM','BDFA','SOS'])
args = parser.parse_args()
db = sqlite3.connect('file:/var/lib/cours-watch/state.db?mode=ro', uri=True)
rows = db.execute('SELECT source,key,payload FROM items WHERE source IN (?,?) ORDER BY source,key',
                  ('moodle:' + args.course, 'teams:' + args.course)).fetchall()
print(json.dumps([{'source': source, **json.loads(payload)} for source, key, payload in rows],
                 ensure_ascii=False, indent=2))
