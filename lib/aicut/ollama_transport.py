"""Consume Ollama streaming responses while preserving the non-streaming API shape."""
import json
import time
import urllib.request
from urllib.parse import urlparse
from .paths import WORK
from .production import atomic_json


def is_local_chat(url):
    parsed = urlparse(url)
    return parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.path == '/api/chat'


def post_stream(url, payload, headers=None, timeout=3600):
    request = urllib.request.Request(url, data=json.dumps(dict(payload, stream=True)).encode('utf-8'),
        headers={'Content-Type': 'application/json', **(headers or {})})
    started = time.monotonic()
    content, thinking = [], []
    chunks = 0
    last_update = 0.0
    path = WORK / 'local-inference-progress.json'
    def report(state, error=None):
        atomic_json(path, {'state': state, 'model': payload.get('model'),
            'elapsed_seconds': round(time.monotonic() - started, 2),
            'response_chunks': chunks, 'response_chars': sum(map(len, content)),
            'error': error, 'updated_at': time.time()})
    report('loading_or_prefill')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for line in response:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get('error'):
                    raise RuntimeError(str(row['error']))
                message = row.get('message') or {}
                content.append(message.get('content', ''))
                thinking.append(message.get('thinking', ''))
                chunks += 1
                if row.get('done'):
                    row['message'] = {'role': 'assistant', 'content': ''.join(content),
                                      'thinking': ''.join(thinking)}
                    report('complete')
                    return row
                if time.monotonic() - last_update >= 1:
                    report('generating')
                    last_update = time.monotonic()
        raise RuntimeError('Ollama stream ended without a completion marker')
    except Exception as exc:
        report('failed', f'{type(exc).__name__}: {str(exc)[:200]}')
        raise
