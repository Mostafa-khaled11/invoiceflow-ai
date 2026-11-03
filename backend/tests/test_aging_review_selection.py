from datetime import UTC, datetime, timedelta


def select_aging_reviews(items, *, now, threshold_minutes=60):
    cutoff = now - timedelta(minutes=threshold_minutes)
    selected = []
    for item in items:
        if not item.get("is_current") or not item.get("is_actionable"):
            continue
        try:
            created_at = datetime.fromisoformat(
                item["created_at"].replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            continue
        if created_at <= cutoff:
            selected.append(item)
    return selected


def review_case(minutes_old, **flags):
    created_at = datetime.now(UTC) - timedelta(minutes=minutes_old)
    return {
        "review_case_id": str(minutes_old),
        "created_at": created_at.isoformat(),
        "is_current": True,
        "is_actionable": True,
        **flags,
    }


def test_empty_queue():
    assert select_aging_reviews([], now=datetime.now(UTC)) == []


def test_younger_case_is_excluded():
    now = datetime.now(UTC)
    assert select_aging_reviews([review_case(59)], now=now) == []


def test_case_at_threshold_is_included():
    now = datetime.now(UTC)
    case = {**review_case(60), "created_at": (now - timedelta(minutes=60)).isoformat()}
    assert select_aging_reviews([case], now=now) == [case]


def test_older_case_is_included():
    case = review_case(61)
    assert select_aging_reviews([case], now=datetime.now(UTC)) == [case]


def test_resolved_historical_invalid_and_multiple_cases_are_filtered():
    now = datetime.now(UTC)
    eligible = review_case(120)
    cases = [
        eligible,
        review_case(120, is_actionable=False),
        review_case(120, is_current=False),
        {**review_case(120), "created_at": "not-a-timestamp"},
        review_case(30),
    ]
    assert select_aging_reviews(cases, now=now) == [eligible]
