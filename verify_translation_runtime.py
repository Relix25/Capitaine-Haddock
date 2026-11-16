"""Real offline engine check, including a new uncached announcement and protected code."""
import json
from pathlib import Path
from translate_worker import Engine

engine = Engine(Path('/opt/cours-watch/models'))
cases = [
    {'key':'proof-en','title':'Presentation','body':'Please upload your presentation on Moodle before 15 November. Each team has 7 minutes.'},
    {'key':'proof-ro','title':'Curs','body':'Ne vedem mâine la curs în sala PR303.'},
    {'key':'proof-fr','title':'Projet','body':'Bonjour, le projet est à rendre le 15 novembre.'},
    {'key':'proof-code','title':'Project commands','body':'Use `python3 split_data.py --dataset pathmnist --num-clients 2` then upload your presentation on Moodle.'},
]
results = [engine.run(item) for item in cases]
assert [r['status'] for r in results] == ['translated','translated','not_needed','translated'], results
assert results[0]['source_language'] == 'en' and 'présentation' in results[0]['body']
assert '15' in results[0]['body'] and '7' in results[0]['body'] and 'Moodle' in results[0]['body']
assert results[1]['source_language'] == 'ro' and 'demain' in results[1]['body'].lower() and 'PR303' in results[1]['body'], results[1]
assert results[2]['body'] == cases[2]['body']
assert '`python3 split_data.py --dataset pathmnist --num-clients 2`' in results[3]['body']
assert all('▁' not in r.get('body','') for r in results)
print(json.dumps({'offline_runtime_checks':4,'passed':True,'new_english_announcement_fr':results[0]['body'],
                  'romanian_fr':results[1]['body'],'existing_french_preserved':True,'command_preserved':True},ensure_ascii=False))
