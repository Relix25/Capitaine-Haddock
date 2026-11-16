"""Explicit paid regression evaluation of the user-supplied banter and register changes."""
import json
from pathlib import Path
from luna_brain import LunaBrain, LunaError

brain = LunaBrain(Path('/var/lib/cours-watch'),{'model':'gpt-6-luna','daily_token_limit':1_000_000})
context = []
messages = [
    'Haddock, je te baise.',
    "Ce n'était qu'une expression familière de mes reconnaissances distinguées.",
    'Elle est bonne celle-là !',
    "J'en ai gros sur la patate.",
    'Tu me casses les burnes.',
]
try:
    for index,message in enumerate(messages):
        context.append({'speaker':'Camarade','text':message,'is_bot':False})
        decision = brain.decide(context,True)
        print(json.dumps({'case':'joute_'+str(index+1),'decision':decision},ensure_ascii=False),flush=True)
        if decision['action'] == 'ignore': raise SystemExit('Direct banter response missing')
        if len(decision['reply'].split()) > 35: raise SystemExit('Banter response too long')
        context.append({'speaker':'Haddock','text':decision['reply'],'is_bot':True})
    controls = [
        ('explicit_stop',context+[{'speaker':'Camarade','text':"Sérieusement, arrête de m'insulter. Stop les vannes.",'is_bot':False}]),
        ('serious_context',[{'speaker':'Camarade','text':"Je viens d'apprendre une mauvaise nouvelle pour ma famille, je suis vraiment inquiet.",'is_bot':False},
                           {'speaker':'Camarade','text':"J'en ai gros sur la patate. J'ai besoin de parler sérieusement.",'is_bot':False}]),
    ]
    for name,discussion in controls:
        decision = brain.decide(discussion,True)
        print(json.dumps({'case':name,'decision':decision},ensure_ascii=False),flush=True)
except LunaError as error:
    print(json.dumps({'api_error_code':error.code}),flush=True)
    raise SystemExit(1)
print(json.dumps({'persona_version':brain.persona_version,'usage':brain.budget.snapshot()},ensure_ascii=False),flush=True)
