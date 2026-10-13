"""Launch the optional desktop GUI from this checkout."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'lib')]


def main() -> int:
    try:
        from gui.main import main as gui_main
        return gui_main()
    except ModuleNotFoundError as exc:
        print(f'Optional dependency missing: {exc.name}. Install the required feature dependencies from requirements-optional.txt.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
