import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metrics import binary_auc, group_resubstitution_accuracy, grouped_binary_auc
from performance_gap_experiment import (
    DiagonalDiscriminant,
    generate_period,
    spearman,
    split_grouped_events,
)
from external_validate_siemens import RunningStats, stable_bucket
from temporal_followup_siemens import business_metrics, select_threshold


class MetricsTest(unittest.TestCase):
    def test_binary_auc_perfect(self):
        self.assertEqual(binary_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)

    def test_binary_auc_ties(self):
        self.assertEqual(binary_auc([0, 1], [0.5, 0.5]), 0.5)

    def test_binary_auc_mixed_ties(self):
        self.assertEqual(binary_auc([0, 1, 0, 1], [0.1, 0.1, 0.2, 0.3]), 0.625)

    def test_grouped_binary_auc_matches_expanded_rows(self):
        groups = {"A": {0: 1, 1: 1}, "B": {0: 1, 1: 0}, "C": {0: 0, 1: 1}}
        self.assertEqual(grouped_binary_auc(groups), 0.625)

    def test_group_resubstitution_accuracy(self):
        rows = [
            {"day": "A", "label": "pass"},
            {"day": "A", "label": "pass"},
            {"day": "A", "label": "fail"},
            {"day": "B", "label": "fail"},
        ]
        self.assertEqual(group_resubstitution_accuracy(rows, "day", "label"), 0.75)

    def test_spearman_perfect(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_group_split_has_no_event_overlap(self):
        rows = generate_period(0, 50, "event_duplication", 1.0, deployment=False)
        train, test = split_grouped_events(rows, 0)
        self.assertFalse({row["event_id"] for row in train} & {row["event_id"] for row in test})

    def test_discriminant_orders_stable_signal(self):
        rows = generate_period(1, 200, "verdict_overlay", 0.0, deployment=False)
        model = DiagonalDiscriminant().fit(rows)
        positive_scores = [model.score(row, memorize_events=False) for row in rows if row["observed_label"] == 1]
        negative_scores = [model.score(row, memorize_events=False) for row in rows if row["observed_label"] == 0]
        self.assertGreater(sum(positive_scores) / len(positive_scores), sum(negative_scores) / len(negative_scores))

    def test_running_stats(self):
        stats = RunningStats()
        for value in (1.0, 2.0, 3.0):
            stats.add(value)
        self.assertEqual(stats.mean, 2.0)
        self.assertEqual(stats.variance, 1.0)

    def test_stable_bucket(self):
        self.assertEqual(stable_bucket("same-event", 7), stable_bucket("same-event", 7))
        self.assertTrue(0 <= stable_bucket("same-event", 7) < 10_000)

    def test_business_threshold_is_fixed_from_positives(self):
        labels = [1] * 100 + [0] * 100
        scores = [float(value) for value in range(100)] + [50.0] * 100
        threshold = select_threshold(labels, scores, target_slip=0.01)
        result = business_metrics(labels, scores, threshold)
        self.assertEqual(threshold, 1.0)
        self.assertEqual(result.slip_rate, 0.01)

    def test_business_metrics_targets(self):
        result = business_metrics([1, 1, 0, 0], [1.0, 1.0, -1.0, 1.0], threshold=0.0)
        self.assertEqual(result.slip_rate, 0.0)
        self.assertEqual(result.volume_reduction, 0.5)
        self.assertTrue(result.target_met)


if __name__ == "__main__":
    unittest.main()
