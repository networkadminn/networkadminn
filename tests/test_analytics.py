from timetrack.analytics import (
    format_clock,
    humanize,
    prepare_activities,
    summarize,
    timeline_buckets,
)
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


def test_idle_counts_toward_arrival_and_left():
    acts = [
        _act("locked", "neutral", 60, idle=True, start=100),
        _act("code", "productive", 30, start=200),
        _act("locked", "neutral", 20, idle=True, start=300),
    ]
    s = summarize(acts)
    assert s.arrival_ts == 100
    assert s.last_seen_ts == 320
    assert s.span_seconds == 220


def test_overlap_dedup_first_wins():
    acts = [
        _act("code", "productive", 60, start=0),
        _act("chrome", "unproductive", 50, start=40),  # overlaps 40–60 → kept as 60–90
    ]
    s = summarize(acts)
    assert s.total_seconds == 90
    assert s.productive_seconds == 60
    assert s.unproductive_seconds == 30


def test_offline_gap_fill_overrides_overlapping_tracker():
    acts = [
        _act("browser", "neutral", 2700, start=0),  # 0–45m neutral
        Activity(
            app="offline",
            title="Client call",
            category="productive",
            idle=False,
            start_ts=2700.0,
            end_ts=4500.0,
            duration=1800.0,
        ),
    ]
    s = summarize(acts)
    assert s.productive_seconds == 1800.0
    assert s.neutral_seconds == 2700.0


def test_day_window_clip():
    acts = [_act("code", "productive", 200, start=50)]
    s = summarize(acts, window_start=100, window_end=150)
    assert s.active_seconds == 50
    assert s.arrival_ts == 100
    assert s.last_seen_ts == 150


def test_productivity_and_effectiveness():
    acts = [
        _act("code", "productive", 75, start=0),
        _act("firefox", "unproductive", 25, start=75),
        _act("locked", "neutral", 100, idle=True, start=100),
    ]
    s = summarize(acts)
    assert s.productivity_pct == 75.0
    assert s.effectiveness_pct == 50.0
    # DeskTime effectiveness may exceed 100 with overtime
    assert s.desktime_effectiveness_pct(50) == 150.0
    assert s.desktime_seconds(30) == 130.0


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


def test_timeline_buckets_split_across_hours():
    acts = [_act("code", "productive", 3600, start=1800)]
    buckets = timeline_buckets(acts, 0, 7200, bucket_seconds=3600)
    assert buckets[0]["productive"] == 1800
    assert buckets[1]["productive"] == 1800


def test_humanize():
    assert humanize(0) == "0s"
    assert humanize(59) == "59s"
    assert humanize(90) == "1m"
    assert humanize(3661) == "1h 1m"


def test_format_clock_ampm():
    assert format_clock(None) == "—"
    text = format_clock(1_700_000_000)
    assert ":" in text
    assert text.endswith("AM") or text.endswith("PM")


def test_productivity_bar_idle_gaps_fillable():
    from timetrack.analytics import idle_gap_list, merge_bar_segments, productivity_bar_segments

    # 10 min productive, then 20 min gap (idle only), then productive again
    acts = [
        _act("code", "productive", 600, start=0),
        _act("locked", "neutral", 1200, idle=True, start=600),
        _act("code", "productive", 300, start=1800),
    ]
    bar_raw = productivity_bar_segments(acts, 0, 3600, bucket_seconds=300.0)
    assert any(s["kind"] == "productive" for s in bar_raw)
    assert any(s.get("fillable") for s in bar_raw)
    gaps = idle_gap_list(bar_raw)
    assert gaps
    assert gaps[0]["duration"] >= 300
    merged = merge_bar_segments(bar_raw)
    assert len(merged) < len(bar_raw)
    assert any(s.get("buckets", 1) > 1 for s in merged)


def test_productivity_bar_gap_splits_at_lunch():
    from timetrack.analytics import idle_gap_list, productivity_bar_segments, reconcile_fillable_gap_bounds

    # Empty from 0–3600s; lunch 3600–5400 would be wrong - use lunch within window
    # 1h bar: activity 0-600, empty rest; lunch 1800-3600 (30-60 min)
    acts = [_act("code", "productive", 600, start=0)]
    lunch = (1800.0, 3600.0)
    bar = productivity_bar_segments(
        acts,
        0,
        5400,
        bucket_seconds=300.0,
        now_ts=5400.0,
        excluded_ranges=[lunch],
    )
    gaps = idle_gap_list(bar)
    assert gaps
    for g in gaps:
        assert g["start"] >= lunch[1] or g["end"] <= lunch[0]
        assert not (g["start"] < lunch[1] and g["end"] > lunch[0])
    fillable = [s for s in bar if s.get("fillable")]
    assert fillable
    assert all(
        not (float(s["start"]) < lunch[1] and float(s.get("end") or s["start"]) > lunch[0])
        for s in fillable
    )
    # Stale gap_start across lunch should be fixed after reconcile
    for s in fillable:
        assert float(s["gap_start"]) >= lunch[1] or float(s["gap_end"]) <= lunch[0]


def test_productivity_bar_no_future_fillable_gaps():
    from timetrack.analytics import idle_gap_list, productivity_bar_segments

    # Activity only in the first 10 minutes; rest of the hour is empty.
    acts = [_act("code", "productive", 600, start=0)]
    # "Now" is 30 minutes in — only the past empty stretch may be fillable.
    bar = productivity_bar_segments(
        acts, 0, 3600, bucket_seconds=300.0, now_ts=1800.0
    )
    fillable = [s for s in bar if s.get("fillable")]
    assert fillable
    assert all(float(s["start"]) < 1800.0 for s in fillable)
    assert all(float(s["gap_end"]) <= 1800.0 for s in fillable)
    assert not any(float(s["start"]) >= 1800.0 and s.get("fillable") for s in bar)
    gaps = idle_gap_list(bar)
    assert gaps
    assert gaps[0]["end"] <= 1800.0
