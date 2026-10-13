"""Synthetic contract tests for review-first audio event collection."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from collect_audio_event_dataset import audio_features, rms_db


class AudioEventDatasetTests(unittest.TestCase):
    def test_signal_features_are_finite_and_timed(self):
        rate = 16000
        t = np.arange(rate, dtype=np.float32) / rate
        x = .2 * np.sin(2 * np.pi * 440 * t)
        x[8000:8010] += .8
        features = audio_features(x, rate)
        self.assertEqual(len(features["short_time_rms_dbfs_50ms"]), 20)
        self.assertGreater(features["transient_peak_candidates"], 0)
        self.assertEqual(set(features["band_energy_fraction"]), {"low_20_250", "mid_250_2k", "high_2k_8k"})
        self.assertAlmostEqual(sum(features["band_energy_fraction"].values()), 1.0, places=6)

    def test_silence_has_finite_floor(self):
        self.assertLess(rms_db(np.zeros(100, dtype=np.float32)), -150)


if __name__ == "__main__":
    unittest.main()
