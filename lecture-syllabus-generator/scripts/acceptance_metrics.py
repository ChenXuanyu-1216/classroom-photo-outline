"""Pure, offline acceptance statistics; importing this module makes no API calls.

The long-term denominator contains only unique live availability-probe run IDs.
Quality pilots and offline checks are descriptive evidence reported separately.
"""

from datetime import datetime, timezone
import math


WILSON_Z_95 = 1.959963984540054
_REQUIRED_FIELDS = ("run_id", "mode", "purpose", "status", "started_at", "finished_at")
_SAMPLING_CAUTION = (
    "Wilson intervals assume independent, representative Bernoulli attempts under "
    "comparable conditions. A unique run_id identifies one logical attempt; it does "
    "not establish statistical independence, and retries must not receive new run IDs. "
    "Observed span does not establish continuous coverage or regular sampling. "
    "Missing planned attempts are not invented. A failed lower-bound acceptance "
    "gate does not prove the true success rate is below the target."
)


def wilson_interval(successes: int, total: int) -> tuple[float, float] | None:
    """Return the two-sided 95% Wilson score interval, or None for no trials.

    Booleans and non-integers are rejected; counts must satisfy
    ``0 <= successes <= total``. No normal-approximation or rounded inputs are used.
    """
    if type(successes) is not int or type(total) is not int:
        raise TypeError("successes and total must be integers, not booleans")
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("counts must satisfy 0 <= successes <= total")
    if total == 0:
        return None

    proportion = successes / total
    z_squared = WILSON_Z_95 ** 2
    denominator = 1 + z_squared / total
    center = (proportion + z_squared / (2 * total)) / denominator
    half_width = WILSON_Z_95 * math.sqrt(
        proportion * (1 - proportion) / total + z_squared / (4 * total ** 2)
    ) / denominator
    return max(0.0, center - half_width), min(1.0, center + half_width)


def _finite_number(value, name: str) -> None:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a finite number, not a boolean")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be finite")


def _timestamp(value, field: str, run_id: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} for {run_id} must be an ISO timestamp string")
    try:
        # Python 3.10's fromisoformat does not yet accept the UTC Z suffix.
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00"
                                        if value.endswith("Z") else value)
    except ValueError as error:
        raise ValueError(f"invalid {field} for {run_id}: expected an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} for {run_id} requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _validated_record(record: dict) -> dict:
    if not isinstance(record, dict):
        raise TypeError("each record must be a dictionary")
    missing = [name for name in _REQUIRED_FIELDS if name not in record]
    if missing:
        raise ValueError(f"record is missing required fields: {', '.join(missing)}")
    run_id = record["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a nonempty string")
    if record["mode"] not in ("live", "offline"):
        raise ValueError(f"unsupported mode for {run_id}")
    if record["purpose"] not in ("quality_pilot", "availability_probe"):
        raise ValueError(f"unsupported purpose for {run_id}")
    if record["status"] not in ("started", "success", "failed"):
        raise ValueError(f"unsupported status for {run_id}")

    normalized = {name: record[name] for name in _REQUIRED_FIELDS}
    normalized["started_at"] = _timestamp(record["started_at"], "started_at", run_id)
    finished = record["finished_at"]
    if record["status"] == "started":
        if finished is not None:
            raise ValueError(f"started record for {run_id} must have finished_at=None")
    else:
        if finished is None:
            raise ValueError(f"terminal record for {run_id} requires finished_at")
        finished = _timestamp(finished, "finished_at", run_id)
        if finished < normalized["started_at"]:
            raise ValueError(f"finished_at precedes started_at for {run_id}")
    normalized["finished_at"] = finished
    return normalized


def _unique_attempts(records: list[dict]) -> list[dict]:
    if not isinstance(records, list):
        raise TypeError("records must be a list of dictionaries")
    unique = {}
    for raw in records:
        current = _validated_record(raw)
        run_id = current["run_id"]
        previous = unique.get(run_id)
        if previous is not None:
            if any(previous[key] != current[key] for key in ("mode", "purpose", "started_at")):
                raise ValueError(f"conflicting identity fields for run_id {run_id}")
            if previous["status"] != "started" and current["status"] != "started":
                if (previous["status"], previous["finished_at"]) != (
                    current["status"], current["finished_at"]
                ):
                    raise ValueError(f"conflicting terminal snapshots for run_id {run_id}")
            # A persisted started snapshot is superseded by its terminal snapshot,
            # regardless of their order in the input. It never becomes a new trial.
            if current["status"] == "started":
                continue
        unique[run_id] = current
    return list(unique.values())


def _metrics(attempts: list[dict]) -> dict:
    total = len(attempts)
    successes = sum(record["status"] == "success" for record in attempts)
    failures = sum(record["status"] == "failed" for record in attempts)
    completed_times = [record["finished_at"] for record in attempts
                       if record["finished_at"] is not None]
    span = None
    if completed_times:
        # Do not use wall-clock time, a declared schedule, or an invented finish
        # for an interrupted attempt as duration evidence.
        span = (max(completed_times) - min(record["started_at"] for record in attempts)
                ).total_seconds() / 3600
    return {
        "sample_size": total,
        "successes": successes,
        "failures": failures,
        "incomplete": total - successes - failures,
        "success_rate": successes / total if total else None,
        "observed_span_hours": span,
        "wilson_interval": wilson_interval(successes, total),
    }


def reliability_summary(records: list[dict], min_hours=168, min_attempts=84, target=0.95) -> dict:
    """Summarize evidence without interpreting a pilot as a long-term campaign.

    Required record keys are run_id/mode/purpose/status/started_at/finished_at.
    Timestamps require an explicit timezone and are compared in UTC. ``started``
    snapshots require a null finish; terminal snapshots require an actual finish.
    Unknown fields are allowed and do not change sampling units. Repeated run IDs
    must describe the same logical attempt. Conflicting terminal outcomes or times
    are errors, since choosing either silently changes the reported evidence.

    Root metrics are ONLY live availability probes. Started attempts remain in
    their denominator as incomplete. Quality pilots are split by live/offline
    mode, and offline availability checks are separate. Span is earliest observed
    start to latest actual finish; with no actual finish it is None.

    No live probes means not_started. Duration or sample shortage means
    insufficient_evidence. With both gates met, pass requires the two-sided 95%
    Wilson lower bound >= target; otherwise fail describes the acceptance gate,
    not a proof that the population rate is below target.
    """
    _finite_number(min_hours, "min_hours")
    _finite_number(target, "target")
    if min_hours < 0:
        raise ValueError("min_hours must be nonnegative")
    if type(min_attempts) is not int:
        raise TypeError("min_attempts must be an integer, not a boolean")
    if min_attempts < 1:
        raise ValueError("min_attempts must be at least 1")
    if not 0 <= target <= 1:
        raise ValueError("target must be between 0 and 1")

    attempts = _unique_attempts(records)
    long_term = _metrics([record for record in attempts
                          if record["mode"] == "live" and record["purpose"] == "availability_probe"])
    interval = long_term["wilson_interval"]
    checks = {
        "duration_met": (long_term["observed_span_hours"] is not None
                         and long_term["observed_span_hours"] >= min_hours),
        "sample_size_met": long_term["sample_size"] >= min_attempts,
        "lower_bound_met": interval is not None and interval[0] >= target,
    }
    if not long_term["sample_size"]:
        status = "not_started"
    elif not checks["duration_met"] or not checks["sample_size_met"]:
        status = "insufficient_evidence"
    else:
        status = "pass" if checks["lower_bound_met"] else "fail"

    return {
        **long_term,
        "long_term_status": status,
        "requirements": {"min_hours": min_hours, "min_attempts": min_attempts, "target": target},
        "checks": checks,
        "quality_pilot": {
            mode: _metrics([record for record in attempts
                            if record["mode"] == mode and record["purpose"] == "quality_pilot"])
            for mode in ("live", "offline")
        },
        "offline_availability_probe": _metrics([
            record for record in attempts
            if record["mode"] == "offline" and record["purpose"] == "availability_probe"
        ]),
        "sampling_caution": _SAMPLING_CAUTION,
    }
