import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metrics import binary_auc, group_resubstitution_accuracy


class MetricsTest(unittest.TestCase):
    def test_binary_auc_perfect(self):
        self.assertEqual(binary_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)

    def test_binary_auc_ties(self):
        self.assertEqual(binary_auc([0, 1], [0.5, 0.5]), 0.5)

    def test_group_resubstitution_accuracy(self):
        rows = [
            {"day": "A", "label": "pass"},
            {"day": "A", "label": "pass"},
            {"day": "A", "label": "fail"},
            {"day": "B", "label": "fail"},
        ]
        self.assertEqual(group_resubstitution_accuracy(rows, "day", "label"), 0.75)


if __name__ == "__main__":
    unittest.main()
