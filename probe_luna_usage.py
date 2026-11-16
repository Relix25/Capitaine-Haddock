"""One explicit tiny API request, printing usage metadata only to reconcile the counter."""
import json
from pathlib import Path
from luna_brain import LunaBrain

brain = LunaBrain(Path('/var/lib/cours-watch'),{'model':'gpt-6-luna','daily_token_limit':1_000_000})
def inspect_usage(payload):
    response = brain.request(payload)
    usage = response.get('usage') or {}
    print(json.dumps({'api_usage':{key:value for key,value in usage.items()
        if key in ('input_tokens','output_tokens','total_tokens','input_tokens_details','output_tokens_details')}}),flush=True)
    return response
brain.transport = inspect_usage
brain.decide([{'speaker':'Test fictif','text':'Haddock, salut.','is_bot':False}],True)
print(json.dumps({'local_usage':brain.budget.snapshot()}),flush=True)
