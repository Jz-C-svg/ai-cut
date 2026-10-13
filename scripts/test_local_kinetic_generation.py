"""Default local flower test now uses the evidence-bound generation gate.
Historical unrestricted probe is preserved with its original test artifacts.
"""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_name('retest_grounded_flower_plan.py')), run_name='__main__')
