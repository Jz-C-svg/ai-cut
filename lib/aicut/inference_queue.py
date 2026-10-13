"""Serialize local GPU inference across AI cut worker processes."""
from contextlib import contextmanager
from urllib.parse import urlparse
from .paths import WORK
from .production import file_lock


@contextmanager
def inference_slot(url: str):
    endpoint = urlparse(url)
    if endpoint.hostname in ('127.0.0.1', 'localhost', '::1') and endpoint.path == '/api/chat':
        with file_lock(WORK / 'locks' / f'local-inference-{endpoint.port or 80}.lock', timeout=7200):
            yield
    else:
        yield
