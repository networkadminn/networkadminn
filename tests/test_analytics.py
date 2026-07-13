from timetrack.analytics import humanize, summarize, timeline_buckets
from timetrack.storage import Activity


def _act(app, category, duration, idle=False, start=0.0):
    return Activity(
        app=app,
        title="",
        category=category,
        idle=idle,
        start_ts=start,
        end_ts=start + duration,
        duration=duration,
    )


def test_summarize_totals():
    acts = [
        _act("code", "productive", 60, start=0),
        _act("firefox", "unproductive", 40, start=60),
        _act("files", "neutral", 20, start=100),
        _act("locked", "neutral", 30, idle=True, start=120),
    ]
    s = summarize(acts)
    assert s.total_seconds == 150
    assert s.active_seconds == 120
    assert s.idle_seconds == 30
    assert s.productive_seconds == 60
    assert s.unproductive_seconds == 40
    assert s.neutral_seconds == 20


def test_productivity_and_effectiveness():
    acts = [
        _act("code", "productive", 75, start=0),
        _act("firefox", "unproductive", 25, start=75),
        _act("locked", "neutral", 100, idle=True, start=100),
    ]
    s = summarize(acts)
    # 75 / (75 + 25) = 75%
    assert s.productivity_pct == 75.0
    # active 100 / total 200 = 50%
    assert s.effectiveness_pct == 50.0


def test_top_apps_sorted():
    acts = [
        _act("code", "productive", 30, start=0),
        _act("code", "productive", 30, start=30),
        _act("firefox", "unproductive", 40, start=60),
    ]
    s = summarize(acts)
    assert s.apps[0].app == "code"
    assert s.apps[0].seconds == 60
    assert s.apps[1].app == "firefox"


def test_empty_summary_no_div_zero():
    s = summarize([])
    assert s.productivity_pct == 0.0
    assert s.effectiveness_pct == 0.0
    assert s.span_seconds == 0.0


def test_timeline_buckets():
    acts = [_act("code", "productive", 600, start=0)]
    buckets = timeline_buckets(acts, 0, 3600, bucket_seconds=1800)
    assert len(buckets) == 2
    assert buckets[0]["productive"] == 600


def test_humanize():
    assert humanize(0) == "0s"
    assert humanize(59) == "59s"
    assert humanize(90) == "1m 30s"
    assert humanize(3661) == "1h 1m"
