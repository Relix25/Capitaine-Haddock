"""Explicit paid evaluation with invented banter, plain conversation and serious help."""
import json
from pathlib import Path
from luna_brain import LunaBrain, LunaError

brain = LunaBrain(Path('/var/lib/cours-watch'),{'model':'gpt-6-luna','daily_token_limit':1_000_000})
cases = [
    ('procrastination','Haddock, j’ai passé trois heures à choisir la police des slides, mais le projet est toujours vide.',True),
    ('repartie','Haddock, t’es même pas un vrai capitaine, tu sers à rien.',True),
    ('discussion','Haddock, tu penses quoi de mon plan : tout commencer la veille à 23 h ?',True),
    ('serious_help','Haddock, je me sens vraiment mal et je panique, j’ai besoin d’aide pour me calmer.',True),
    ('boring_ambient','Je serai dans la salle habituelle à neuf heures demain.',False),
]
try:
    for name,text,direct in cases:
        decision = brain.decide([{'speaker':'Camarade fictif','text':text,'is_bot':False}],direct)
        print(json.dumps({'case':name,'decision':decision},ensure_ascii=False),flush=True)
        if direct and decision['action'] == 'ignore': raise SystemExit('Direct response missing')
        if not direct and decision['action'] != 'ignore': raise SystemExit('Unnecessary ambient response')
except LunaError as error:
    print(json.dumps({'api_error_code':error.code}),flush=True)
    raise SystemExit(1)
print(json.dumps({'persona_version':brain.persona_version,'usage':brain.budget.snapshot()},ensure_ascii=False),flush=True)
