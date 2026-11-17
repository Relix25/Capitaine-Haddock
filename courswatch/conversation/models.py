"""Allow-listed, durable model selection; no credentials or conversation content."""
import json
import os
from pathlib import Path
import tempfile

MODEL_CHOICES = {
    'hy4': {'model': 'tencent/hy4-flash', 'require_zdr': False},
    'deepseek': {'model': 'deepseek/deepseek-v4.1-flash', 'require_zdr': True},
}
MODEL_LABELS = {'hy4': 'Tencent HY4 Flash', 'deepseek': 'DeepSeek V4.1 Flash'}


def effective_settings(data, settings):
    result = dict(settings)
    if result.get('provider', 'openai') != 'commandcode':
        return result
    try:
        selection = json.loads((Path(data) / 'model-selection.json').read_text())
    except FileNotFoundError:
        return result
    if (not isinstance(selection, dict) or set(selection) not in ({'selection'}, {'selection','debug'})
            or not isinstance(selection['selection'], str) or selection['selection'] not in MODEL_CHOICES
            or not isinstance(selection.get('debug', True), bool)):
        raise ValueError('Invalid model selection')
    return {**result, **MODEL_CHOICES[selection['selection']], 'debug':selection.get('debug', True)}


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
