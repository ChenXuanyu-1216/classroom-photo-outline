"""Acceptance statistics must not turn a small pilot into reliability evidence."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "acceptance_metrics.py"


def metrics():
    return runpy.run_path(str(SCRIPT))


def record(run_id="probe-1", *, status="success", mode="live",
           purpose="availability_probe", started_at="2026-10-01T00:00:00Z",
           finished_at="2026-10-01T00:01:00Z", **metadata):
    return {"run_id": run_id, "mode": mode, "purpose": purpose,
            "status": status, "started_at": started_at,
            "finished_at": finished_at, **metadata}


def probe_series(count, *, successes=None, hours=168):
    """Create known logical attempts with an exact, explicit observation span."""
    successes = count if successes is None else successes
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    records = []
    for index in range(count):
        instant = start + timedelta(hours=hours * index / (count - 1))
        records.append(record(
            f"probe-{index}", status="success" if index < successes else "failed",
            started_at=instant.isoformat(), finished_at=instant.isoformat(),
        ))
    return records


@pytest.mark.parametrize(("successes", "total", "expected"), [
    (0, 0, None),
    (1, 1, (0.2065493144, 1.0)),
    (0, 1, (0.0, 0.7934506856)),
    (100, 100, (0.9630065018, 1.0)),
    (50, 100, (0.4038315304, 0.5961684696)),
    (0, 100, (0.0, 0.0369934982)),
])
def test_wilson_interval_matches_known_95_percent_literals(successes, total, expected):
    result = metrics()["wilson_interval"](successes, total)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(("successes", "total", "error"), [
    (-1, 1, ValueError), (2, 1, ValueError), (0, -1, ValueError),
    (1, 0, ValueError), (True, 1, TypeError), (1, False, TypeError),
    (0.5, 1, TypeError), (0, "1", TypeError),
])
def test_wilson_interval_rejects_invalid_counts(successes, total, error):
    with pytest.raises(error):
        metrics()["wilson_interval"](successes, total)


def test_empty_evidence_returns_na_and_not_started():
    result = metrics()["reliability_summary"]([])
    assert result["long_term_status"] == "not_started"
    assert result["sample_size"] == 0
    assert (result["successes"], result["failures"], result["incomplete"]) == (0, 0, 0)
    assert result["success_rate"] is None
    assert result["observed_span_hours"] is None
    assert result["wilson_interval"] is None
    assert not any(result["checks"].values())


def test_one_success_does_not_pass_long_term_acceptance():
    result = metrics()["reliability_summary"]([record()])
    assert result["sample_size"] == 1
    assert result["success_rate"] == 1.0
    assert result["wilson_interval"] == pytest.approx((0.2065493144, 1.0))
    assert result["long_term_status"] == "insufficient_evidence"
    assert result["observed_span_hours"] == pytest.approx(1 / 60)


def test_quality_pilots_and_offline_probes_never_enter_long_term_denominator():
    result = metrics()["reliability_summary"]([
        record("quality-live", purpose="quality_pilot"),
        record("quality-offline", mode="offline", purpose="quality_pilot", status="failed"),
        record("probe-offline", mode="offline"),
    ])
    assert result["sample_size"] == 0
    assert result["long_term_status"] == "not_started"
    assert result["quality_pilot"]["live"]["sample_size"] == 1
    assert result["quality_pilot"]["live"]["successes"] == 1
    assert result["quality_pilot"]["offline"]["failures"] == 1
    assert result["offline_availability_probe"]["sample_size"] == 1


def test_unfinished_attempts_stay_in_denominator_without_inventing_finish_times():
    result = metrics()["reliability_summary"]([
        record("ok"), record("failed", status="failed"),
        record("unfinished", status="started", finished_at=None,
               started_at="2026-10-09T00:00:00Z", declared_schedule_hours=168),
    ])
    assert result["sample_size"] == 3
    assert (result["successes"], result["failures"], result["incomplete"]) == (1, 1, 1)
    assert result["success_rate"] == pytest.approx(1 / 3)
    assert result["wilson_interval"] == pytest.approx((0.0614919447, 0.7923403992))
    assert result["observed_span_hours"] == pytest.approx(1 / 60)
    assert result["long_term_status"] == "insufficient_evidence"


def test_only_unfinished_attempt_has_no_observed_finish_span():
    result = metrics()["reliability_summary"]([
        record(status="started", finished_at=None),
    ])
    assert result["sample_size"] == 1
    assert result["incomplete"] == 1
    assert result["success_rate"] == 0
    assert result["observed_span_hours"] is None
    assert result["long_term_status"] == "insufficient_evidence"


@pytest.mark.parametrize("reverse", [False, True])
def test_snapshots_and_retries_with_same_run_id_count_as_one_attempt(reverse):
    records = [record(status="started", finished_at=None, retry_number=0),
               record(retry_number=1), record(retry_number=2)]
    if reverse:
        records.reverse()
    result = metrics()["reliability_summary"](records)
    assert result["sample_size"] == 1
    assert (result["successes"], result["failures"], result["incomplete"]) == (1, 0, 0)
    assert result["wilson_interval"][0] == pytest.approx(0.2065493144)


def test_identical_unfinished_snapshots_still_count_as_one_incomplete_attempt():
    unfinished = record(status="started", finished_at=None)
    result = metrics()["reliability_summary"]([unfinished, dict(unfinished)])
    assert result["sample_size"] == result["incomplete"] == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_opposite_terminal_statuses_are_rejected_instead_of_picking_success(reverse):
    records = [record(status="failed"), record(status="success")]
    if reverse:
        records.reverse()
    with pytest.raises(ValueError, match="conflicting.*probe-1"):
        metrics()["reliability_summary"](records)


@pytest.mark.parametrize("changed", [
    {"mode": "offline"}, {"purpose": "quality_pilot"},
    {"started_at": "2026-10-01T00:00:01Z"},
    {"finished_at": "2026-10-01T00:02:00Z"},
])
def test_duplicate_identity_or_terminal_time_conflicts_cannot_bias_the_span(changed):
    with pytest.raises(ValueError, match="conflicting.*probe-1"):
        metrics()["reliability_summary"]([record(), record(**changed)])


def test_equivalent_timezone_offsets_merge_as_same_instant():
    records = [record(), record(started_at="2026-10-01T08:00:00+08:00",
                                finished_at="2026-10-01T08:01:00+08:00")]
    result = metrics()["reliability_summary"](records)
    assert result["sample_size"] == 1
    assert result["observed_span_hours"] == pytest.approx(1 / 60)


def test_many_successes_in_a_short_window_still_lack_long_term_evidence():
    result = metrics()["reliability_summary"](probe_series(100, hours=1))
    assert result["sample_size"] == 100
    assert result["wilson_interval"][0] == pytest.approx(0.9630065018)
    assert result["checks"] == {"duration_met": False, "sample_size_met": True,
                                "lower_bound_met": True}
    assert result["long_term_status"] == "insufficient_evidence"


def test_long_span_does_not_replace_minimum_attempt_count():
    result = metrics()["reliability_summary"](probe_series(2))
    assert result["observed_span_hours"] == 168
    assert result["sample_size"] == 2
    assert result["long_term_status"] == "insufficient_evidence"


def test_default_gate_passes_only_with_duration_sample_count_and_lower_bound():
    result = metrics()["reliability_summary"](probe_series(84))
    assert result["observed_span_hours"] == 168
    assert result["sample_size"] == 84
    assert all(result["checks"].values())
    assert result["long_term_status"] == "pass"


def test_observed_95_percent_success_fails_a_95_percent_lower_bound_gate():
    result = metrics()["reliability_summary"](probe_series(100, successes=95))
    assert result["success_rate"] == 0.95
    assert result["wilson_interval"] == pytest.approx((0.8882495308, 0.9784563208))
    assert result["checks"] == {"duration_met": True, "sample_size_met": True,
                                "lower_bound_met": False}
    assert result["long_term_status"] == "fail"


def test_custom_acceptance_requirements_are_applied_and_reported():
    result = metrics()["reliability_summary"](
        [record()], min_hours=0, min_attempts=1, target=0.2)
    assert result["requirements"] == {"min_hours": 0, "min_attempts": 1, "target": 0.2}
    assert result["long_term_status"] == "pass"
    assert "independent" in result["sampling_caution"].lower()
    assert "continuous" in result["sampling_caution"].lower()


@pytest.mark.parametrize("options", [
    {"min_hours": -1}, {"min_hours": float("nan")}, {"min_hours": float("inf")},
    {"min_hours": True}, {"min_attempts": 0}, {"min_attempts": 1.5},
    {"min_attempts": True}, {"target": -0.1}, {"target": 1.1},
    {"target": float("nan")}, {"target": True},
])
def test_invalid_acceptance_requirements_raise_clear_errors(options):
    with pytest.raises((TypeError, ValueError)):
        metrics()["reliability_summary"]([], **options)


@pytest.mark.parametrize("changed", [
    {"run_id": ""}, {"run_id": 1}, {"mode": "unknown"},
    {"purpose": "unknown"}, {"status": "passed"},
    {"started_at": "not-a-date"}, {"started_at": "2026-10-01T00:00:00"},
    {"finished_at": "2026-10-01T00:01:00"},
    {"finished_at": "2026-09-30T23:00:00Z"}, {"finished_at": None},
    {"status": "started"},
])
def test_invalid_records_are_rejected_instead_of_silently_dropped(changed):
    with pytest.raises((TypeError, ValueError)):
        metrics()["reliability_summary"]([record(**changed)])


def test_record_requires_all_audit_fields_even_when_finished_at_is_null():
    incomplete = record(status="started", finished_at=None)
    del incomplete["finished_at"]
    with pytest.raises(ValueError, match="finished_at"):
        metrics()["reliability_summary"]([incomplete])


def test_naive_timestamps_explain_the_required_timezone():
    with pytest.raises(ValueError, match="timezone"):
        metrics()["reliability_summary"]([record(started_at="2026-10-01T00:00:00")])


def test_summarizing_does_not_mutate_the_persisted_records():
    sample = record(extra={"request_count": 3})
    snapshot = {**sample, "extra": dict(sample["extra"])}
    metrics()["reliability_summary"]([sample])
    assert sample == snapshot
