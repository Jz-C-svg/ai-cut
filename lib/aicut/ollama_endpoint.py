"""One Ollama address for model discovery, health checks and inference."""
import json
import os
from urllib.parse import urlparse

DEFAULT_BASE_URL = 'http://127.0.0.1:11434'


def ollama_base_url():
    value = os.environ.get('AICUT_OLLAMA_BASE_URL', '').strip()
    if not value:
        from .paths import CONFIG_DIR
        path = CONFIG_DIR / 'aicut.json'
        cfg = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        value = str(cfg.get('ollama_base_url') or DEFAULT_BASE_URL).strip()
    parsed = urlparse(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('Ollama address must be a base URL without credentials or API path')
    return value.rstrip('/')


def ollama_url(path):
    if not path.startswith('/api/'):
        raise ValueError('Expected an Ollama API path')
    return ollama_base_url() + path
