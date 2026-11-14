import numpy as np

from scripts.validate_condition_lift import paired_bootstrap_delta


def test_paired_cell_bootstrap_is_reproducible_and_zero_for_identical_scores():
    labels = np.array([0, 1, 0, 1, 1, 0, 0, 1], dtype=np.int8)
    scores = np.array([0.1, 0.8, 0.2, 0.7, 0.9, 0.3, 0.4, 0.6])
    first = paired_bootstrap_delta(labels, scores, scores, replicates=200, seed=17)
    second = paired_bootstrap_delta(labels, scores, scores, replicates=200, seed=17)
    assert first == second
    assert first["lifted_minus_baseline_auc_ci95"] == [0.0, 0.0]
    assert first["lifted_minus_baseline_precision_top_5_percent_ci95"] == [0.0, 0.0]


def test_paired_cell_bootstrap_rejects_too_few_replicates():
    labels = np.array([0, 1])
    try:
        paired_bootstrap_delta(labels, labels, labels, replicates=10)
    except ValueError as exc:
        assert "at least 100" in str(exc)
    else:
        raise AssertionError("expected too few bootstrap replicates to fail")
