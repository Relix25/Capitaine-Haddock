"""Measured model streams and bounded Discord subtext; technical metadata only."""
import itertools
import json
import math
import time

MAX_RESPONSE_BYTES = 2_000_000


class MeasuredResult(dict):
    """Keep technical metadata outside the model's three-field decision schema."""
    def __init__(self, value, metrics=None):
        super().__init__(value)
        self.metrics = dict(metrics or {})


def _number(value):
    return (value if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 else None)


def _tokens(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _events(lines):
    parts, size = [], 0
    for raw in lines:
        line = raw.decode('utf-8').rstrip('\r\n')
        if not line:
            if parts:
                data = '\n'.join(parts)
                parts, size = [], 0
                yield data if data == '[DONE]' else json.loads(data)
            continue
        if line.startswith('data:'):
            data = line[5:].lstrip(' ')
            size += len(raw)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError('Oversized model stream event')
            parts.append(data)
    if parts:
        data = '\n'.join(parts)
        yield data if data == '[DONE]' else json.loads(data)


def _chat_response(model, usage, content, reason, refusal):
    usage = usage or {}
    output = {'type': 'refusal'} if refusal or reason == 'content_filter' else {
        'type': 'output_text', 'text': content}
    status = 'completed' if reason in (None, 'stop', 'content_filter') else 'incomplete'
    return {'model': model, 'status': status,
            'incomplete_details': {'reason': 'max_output_tokens' if reason == 'length' else reason},
            'output': [{'type': 'message', 'content': [output]}],
            'usage': {'input_tokens': usage.get('prompt_tokens'), 'output_tokens': usage.get('completion_tokens'),
                      'total_tokens': usage.get('total_tokens'), 'input_tokens_details': usage.get('prompt_tokens_details'),
                      'output_tokens_details': usage.get('completion_tokens_details')}}


def _read_chat_events(events, started, clock):
    pieces = []
    first_text = model = usage = finish = None
    refusal = False
    for event in events:
        if event == '[DONE]':
            return MeasuredResult(_chat_response(model, usage, ''.join(pieces), finish, refusal), {
                'ttft_ms': None if first_text is None else (first_text - started) * 1000,
                'api_ms': (clock() - started) * 1000})
        if not isinstance(event, dict) or event.get('error'):
            raise ValueError('Chat completion stream failed')
        model = event.get('model') or model
        usage = event.get('usage') or usage
        for choice in event.get('choices', []):
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta') or {}
            text = delta.get('content') or ''
            if not isinstance(text, str):
                raise ValueError('Invalid chat completion delta')
            pieces.append(text)
            if first_text is None and text.strip():
                first_text = clock()
            refusal = refusal or bool(delta.get('refusal'))
            finish = choice.get('finish_reason') or finish
    raise ValueError('Chat stream ended without terminal response')


def read_model_response(stream, started, clock=time.perf_counter, *, chat=False,
                        deadline_seconds=30, deadline_clock=time.monotonic):
    """Time the first nonempty text delta, never headers or placeholder events."""
    deadline = deadline_clock() + deadline_seconds

    def check_deadline():
        if deadline_clock() >= deadline:
            raise TimeoutError('Model response deadline exceeded')

    check_deadline()
    first_line = stream.readline(MAX_RESPONSE_BYTES + 1)
    while first_line and not first_line.strip():
        check_deadline()
        first_line = stream.readline(MAX_RESPONSE_BYTES + 1)
    if len(first_line) > MAX_RESPONSE_BYTES:
        raise ValueError('Oversized model response')
    if not first_line.lstrip().startswith((b'data:', b'event:', b':')):
        # A non-streaming gateway has no measurable first-token time.
        raw = first_line + stream.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('Oversized model response')
        value = json.loads(raw)
        if chat:
            choices = value.get('choices') if isinstance(value, dict) else None
            if not choices or value.get('error'):
                raise ValueError('Invalid chat completion')
            message = choices[0]['message']
            value = _chat_response(value.get('model'), value.get('usage'), message.get('content') or '',
                                   choices[0].get('finish_reason'), message.get('refusal'))
        return MeasuredResult(value, {'ttft_ms': None, 'api_ms': (clock() - started) * 1000})

    def remaining_lines():
        aggregate = len(first_line)
        while True:
            check_deadline()
            raw = stream.readline(MAX_RESPONSE_BYTES + 1)
            if not raw:
                return
            aggregate += len(raw)
            if len(raw) > MAX_RESPONSE_BYTES or aggregate > MAX_RESPONSE_BYTES:
                raise ValueError('Oversized model stream line')
            yield raw

    first_text, pieces, text_size = None, [], 0
    events = _events(itertools.chain([first_line], remaining_lines()))
    if chat:
        return _read_chat_events(events, started, clock)
    for event in events:
        check_deadline()
        if event == '[DONE]':
            break
        kind = event.get('type')
        if kind == 'response.output_text.delta':
            delta = event.get('delta', '')
            if isinstance(delta, str):
                text_size += len(delta.encode('utf-8'))
                if text_size > MAX_RESPONSE_BYTES:
                    raise ValueError('Oversized model output')
                pieces.append(delta)
                if first_text is None and delta.strip():
                    first_text = clock()
        elif kind in ('response.completed', 'response.incomplete', 'response.failed'):
            completed = clock()
            response = event.get('response')
            if not isinstance(response, dict):
                raise ValueError('Invalid terminal model response')
            response.setdefault('status', kind.removeprefix('response.'))
            if pieces and not response.get('output'):
                response['output'] = [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': ''.join(pieces)}]}]
            return MeasuredResult(response, {
                'ttft_ms': (first_text - started) * 1000 if first_text is not None else None,
                'api_ms': (completed - started) * 1000,
            })
        elif kind == 'error':
            raise ValueError('Model stream failed')
    raise ValueError('Model stream ended without terminal response')


def response_metrics(response, model, elapsed_ms):
    recorded = getattr(response, 'metrics', {})
    duration = _number(recorded.get('api_ms'))
    duration = duration if duration is not None else _number(elapsed_ms)
    first = _number(recorded.get('ttft_ms'))
    if first is not None and (duration is None or first > duration):
        first = None
    usage = response.get('usage') or {}
    incoming, outgoing = _tokens(usage.get('input_tokens')), _tokens(usage.get('output_tokens'))
    total = _tokens(usage.get('total_tokens'))
    if total is None and incoming is not None and outgoing is not None:
        total = incoming + outgoing
    cached = _tokens((usage.get('input_tokens_details') or {}).get('cached_tokens'))
    # Streaming chunks can contain several tokens, so this is an estimate of
    # output throughput after the first text delta, including JSON envelope tokens.
    reasoning = _tokens((usage.get('output_tokens_details') or {}).get('reasoning_tokens')) or 0
    visible = outgoing - reasoning if outgoing is not None else None
    span = duration - first if duration is not None and first is not None else None
    tps = ((visible - 1) * 1000 / span
           if visible is not None and visible > 1 and span is not None and span >= 20 else None)
    return {'model': model, 'ttft_ms': first, 'api_ms': duration,
            'input_tokens': incoming, 'output_tokens': outgoing, 'total_tokens': total,
            'cached_input_tokens': cached, 'output_tps': tps}


def local_metrics():
    return {'ttft_ms': None, 'api_ms': 0, 'total_tokens': 0,
            'input_tokens': 0, 'output_tokens': 0, 'output_tps': None}


def _seconds(value):
    value = _number(value)
    return ('{:.2f} s'.format(value / 1000).replace('.', ',') if value is not None else 'n/d')


def _count(value):
    return format(value, ',').replace(',', '\u202f')


def footer(metrics, local_response=False):
    labels = {'deepseek/deepseek-v4.1-flash': 'DeepSeek V4.1 Flash',
              'tencent/hy4-flash': 'Tencent HY4 Flash'}
    name = labels.get(metrics.get('model'), 'Local' if local_response else 'IA')
    parts = [name, 'TTFT ' + _seconds(metrics.get('ttft_ms')),
             'Total IA ' + _seconds(metrics.get('api_ms'))]
    tps = _number(metrics.get('output_tps'))
    parts.append(('≈ {:.1f} tok/s'.format(tps).replace('.', ',') if tps is not None else 'TPS n/d'))
    total = _tokens(metrics.get('total_tokens'))
    token_text = _count(total) + ' tokens' if total is not None else 'tokens non confirmés'
    incoming, outgoing = _tokens(metrics.get('input_tokens')), _tokens(metrics.get('output_tokens'))
    if incoming is not None and outgoing is not None and incoming + outgoing:
        details = _count(incoming) + ' entrée + ' + _count(outgoing) + ' sortie'
        cached = _tokens(metrics.get('cached_input_tokens'))
        if cached:
            details += ' ; cache ' + _count(cached)
        token_text += ' (' + details + ')'
    parts.append(token_text)
    if local_response and name != 'Local':
        parts.append('réponse locale')
    return '-# ' + ' · '.join(parts)


def reply_with_footer(text, metrics, local_response=False):
    subtext = footer(metrics, local_response)
    available = max(0, 2000 - len(subtext) - 2)
    body = str(text).strip()[:min(1800, available)]
    # A truncated code answer must not swallow the subtext into its code block.
    if body.count('```') % 2:
        body = body[:max(0, available - 4)].rstrip()
        if body.count('```') % 2:
            body += '\n```'
    return body.rstrip() + '\n\n' + subtext
