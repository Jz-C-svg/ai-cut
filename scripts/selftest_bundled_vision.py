#!/usr/bin/env python3
"""Check the fixed local vision bundle without loading the multi-GB model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from aicut import bundled_vision  # noqa: E402


def main() -> int:
    status = bundled_vision.health()
    required = ("runner", "model", "mmproj")
    assert all(Path(str(status[key])).exists() for key in required), status
    assert status["available"] is True
    try:
        bundled_vision._validate({"??": "??"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("invalid model JSON was accepted")
    print(json.dumps({"pass": True, "model": status["id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
