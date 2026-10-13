"""Resolve source-release defaults without recording a developer's machine paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def portable_path(value: str) -> str:
    """Expand an internal project:/user: marker to a platform-native path."""
    scheme, separator, rest = value.partition(':')
    if separator and scheme in {'project', 'user'}:
        base = ROOT if scheme == 'project' else Path.home()
        return str(base.joinpath(*rest.replace('\\', '/').split('/')))
    return value
