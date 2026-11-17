"""Common proof conventions for persisted and returned observation records."""
import hashlib
import json


def observation_digest(observation):
    record = dict(observation)
    for key in ('metrics', 'cache_hit', 'prior_observation'):
        record.pop(key, None)
    legacy_date = record.pop('analysed_at', None)
    if legacy_date:
        record.setdefault('analyzed_at', legacy_date)
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def observation_limitations(observation):
    limits = tuple(observation.get('limitations', ()))
    if (observation.get('actual_format') == 'GIF'
            and observation.get('provenance', {}).get('preprocess_version') == 'timed-animation-v1'):
        limits += ('Observation antérieure à la vérification de l’intégrité du GIF ; sa complétude est inconnue.',)
    return limits
