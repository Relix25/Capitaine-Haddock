"""Allow-listed, durable model selection; no credentials or conversation content."""
import json
import os
from pathlib import Path
import tempfile

from courswatch.conversation.contracts import ModelCapabilities, ModelProfile

ENDPOINT = 'https://api.commandcode.ai/provider/v1/responses'

MODEL_CHOICES = {
    'hy4': {'model': 'tencent/hy4-flash', 'require_zdr': False},
    'deepseek': {'model': 'deepseek/deepseek-v4.1-flash', 'require_zdr': True},
}
MODEL_LABELS = {'hy4': 'Tencent HY4 Flash', 'deepseek': 'DeepSeek V4.1 Flash'}
DECLARED_VISION = 'Owner confirms model vision, 2026-11-17; API combination untested'


def resolve_profile(settings):
    """Freeze one explicit profile and its reviewed capability record.

    Owner confirmation establishes model vision declarations for both models.
    It does not establish support of images plus strict JSON on this API route.
    """
    if settings.get('provider') != 'commandcode':
        raise ValueError('Unsupported conversation provider; configure commandcode explicitly')
    model = settings.get('model')
    profile_id = next((name for name, value in MODEL_CHOICES.items() if value['model'] == model), None)
    if profile_id is None:
        raise ValueError('Configure an explicit supported conversation model')
    require_zdr = settings.get('require_zdr', MODEL_CHOICES[profile_id]['require_zdr'])
    if not isinstance(require_zdr, bool):
        raise ValueError('require_zdr must be a boolean')
    if profile_id == 'deepseek' and not require_zdr:
        raise ValueError('DeepSeek requires ZDR; no silent privacy downgrade')
    records = settings.get('model_capabilities', {})
    if not isinstance(records, dict):
        raise ValueError('Invalid capability configuration')
    record = records.get(profile_id, {})
    if not isinstance(record, dict):
        raise ValueError('Invalid capability record')
    states = {'unknown', 'declared', 'verified', 'unsupported'}
    values = {name: record.get(name, 'declared' if name == 'image_input' else 'unknown')
              for name in ('image_input', 'multi_image', 'structured_output',
                           'api_image_input', 'api_structured_output')}
    if any(not isinstance(value,str) or value not in states for value in values.values()):
        raise ValueError('Invalid capability state')
    verified = record.get('verified_real', False)
    evidence = record.get('evidence', '' if verified else DECLARED_VISION)
    if not isinstance(verified, bool) or not isinstance(evidence, str):
        raise ValueError('Invalid capability evidence')
    if verified:
        if (not evidence.strip() or evidence.strip() == DECLARED_VISION
                or record.get('provider') != 'commandcode'
                or record.get('endpoint') != ENDPOINT or record.get('requested_model_id') != model
                or record.get('require_zdr') is not require_zdr
                or any(values[name] != 'verified' for name in
                       ('image_input', 'structured_output', 'api_image_input', 'api_structured_output'))):
            raise ValueError('Real capability verification must identify model, API, JSON and privacy policy')
    formats = record.get('formats', ('image/png', 'image/jpeg', 'image/webp'))
    if (not isinstance(formats, (list, tuple)) or not formats or
            any(value not in ('image/png', 'image/jpeg', 'image/webp') for value in formats)):
        raise ValueError('Unsupported visual input format; decode animation into fixed frames')
    maximum = record.get('max_payload_bytes', 8 * 1024 * 1024)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1024 <= maximum <= 32 * 1024 * 1024:
        raise ValueError('Invalid visual payload limit')
    return ModelProfile(profile_id, 'commandcode', ENDPOINT, model, require_zdr,
                        str(settings.get('profile_config_version', '1')),
                        ModelCapabilities(**values, verified_real=verified, evidence=evidence,
                                          formats=tuple(formats), max_payload_bytes=maximum))


def verified_vision(profile, *, multiple=False):
    if profile is None:
        return False
    cap = profile.capabilities
    fields = ('image_input', 'structured_output', 'api_image_input', 'api_structured_output')
    if multiple:
        fields += ('multi_image',)
    return bool(cap.verified_real and cap.evidence and all(getattr(cap, key) == 'verified' for key in fields))


def resolve_vision_profile(settings, turn_profile):
    route = settings.get('vision_route')
    if route is None or route == turn_profile.profile_id:
        return turn_profile if verified_vision(turn_profile) else None
    if route not in MODEL_CHOICES:
        raise ValueError('Unsupported explicit vision route')
    route_settings = {**settings, **MODEL_CHOICES[route]}
    if 'vision_route_require_zdr' in settings:
        route_settings['require_zdr'] = settings['vision_route_require_zdr']
    # A turn requiring ZDR must never disclose media to a weaker route.
    if turn_profile.require_zdr and route_settings['require_zdr'] is False:
        return None
    target = resolve_profile(route_settings)
    return target if verified_vision(target) else None


def settings_for_selection(settings, selection, debug):
    """Apply a preference to the original configuration and retain its ZDR floor."""
    if selection not in MODEL_CHOICES or not isinstance(debug, bool):
        raise ValueError('Unsupported model selection')
    configured_zdr = settings.get('require_zdr', False)
    if not isinstance(configured_zdr, bool):
        raise ValueError('require_zdr must be a boolean')
    choice = MODEL_CHOICES[selection]
    return {**settings, 'model':choice['model'], 'debug':debug,
            'require_zdr':configured_zdr or choice['require_zdr']}


def effective_settings(data, settings):
    result = dict(settings)
    if result.get('provider') != 'commandcode':
        return result
    try:
        selection = json.loads((Path(data) / 'model-selection.json').read_text())
    except FileNotFoundError:
        return result
    if (not isinstance(selection, dict) or set(selection) not in ({'selection'}, {'selection','debug'})
            or not isinstance(selection['selection'], str) or selection['selection'] not in MODEL_CHOICES
            or not isinstance(selection.get('debug', True), bool)):
        raise ValueError('Invalid model selection')
    return settings_for_selection(result, selection['selection'], selection.get('debug', True))


def save_preferences(data, selection, debug):
    if selection not in MODEL_CHOICES or not isinstance(debug, bool):
        raise ValueError('Unsupported model selection')
    directory = Path(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.model-selection-', delete=False) as stream:
            temporary = Path(stream.name)
            temporary.chmod(0o600)
            json.dump({'selection': selection, 'debug': debug}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / 'model-selection.json')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
