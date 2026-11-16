"""Prepare the first managed view from the four already checked course snapshots."""
import json
from pathlib import Path

root=Path(__file__).resolve().parents[2]
directory=root/'.extraction/Actualisation_2026-11-12/snapshots'
courses={}
checked=[]
for identity in ('AAIT','AITDM','BDFA','SOS'):
    data=json.loads((directory/(identity+'.json')).read_text(encoding='utf-8-sig'))
    checked.append(data['latest_collection_utc'])
    courses[identity]={'label':identity,'sources':[
        {key:source[key] for key in ('source','status','checked_utc') if key in source}
        for source in data['sources']],
        'items':[{key:item[key] for key in ('source','key','title','body','url','fingerprint') if key in item} for item in data['items']]}
target=directory/'coffre-pedagogique.json'
target.write_text(json.dumps({'version':1,'checked_utc':max(checked),'courses':courses},ensure_ascii=False,indent=2),encoding='utf-8')
print(target)
