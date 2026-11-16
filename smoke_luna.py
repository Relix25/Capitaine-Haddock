"""Opt-in real API smoke test using invented messages and the production token ledger."""
import json
from pathlib import Path
from haddock_voice import load_samples
from luna_brain import LunaBrain, LunaError


def main():
    brain = LunaBrain(Path('/var/lib/cours-watch'),{'model':'gpt-6-luna','daily_token_limit':1_000_000})
    cases = [
        ('direct',[{'speaker':'Camarade fictif','text':'Haddock, j’ai vingt minutes avant un oral et je panique. Donne-moi trois étapes concrètes.','is_bot':False}],True),
        ('followup',[{'speaker':'Camarade fictif','text':'Je prépare un exposé sur les anomalies industrielles, on travaille à deux.','is_bot':False},
                     {'speaker':'Haddock','text':'Répartissez-vous le problème, les données et une démonstration simple.','is_bot':True},
                     {'speaker':'Camarade fictif','text':'Et comment on répartit le travail entre nous ?','is_bot':False}],True),
        ('boring_ambient',[{'speaker':'Camarade fictif','text':'Le cours commence à neuf heures dans la salle habituelle.','is_bot':False},
                           {'speaker':'Autre camarade fictif','text':'D’accord, j’y serai à neuf heures.','is_bot':False}],False),
    ]
    try:
        for name,context,direct in cases:
            result = brain.decide(context,direct,load_samples())
            print(json.dumps({'case':name,'decision':result},ensure_ascii=False),flush=True)
            if direct and result['action'] == 'ignore': raise SystemExit('Direct response ignored')
            if not direct and result['action'] != 'ignore': raise SystemExit('Boring ambient was not ignored')
    except LunaError as error:
        print(json.dumps({'api_error_code':error.code}),flush=True)
        raise SystemExit(1)
    print(json.dumps({'usage':brain.budget.snapshot()},ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
