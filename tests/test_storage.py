from timetrack.storage import Storage


def test_record_and_query():
    with Storage(":memory:") as s:
        base = 1_000_000.0
        s.record("code", "a.py", "productive", False, 5.0, now=base + 5)
        rows = s.all()
        assert len(rows) == 1
        assert rows[0].app == "code"
        assert rows[0].duration == 5.0


def test_contiguous_same_activity_merges():
    with Storage(":memory:") as s:
        base = 1_000_000.0
        s.record("code", "a.py", "productive", False, 5.0, now=base + 5)
        s.record("code", "a.py", "productive", False, 5.0, now=base + 10)
        rows = s.all()
        assert len(rows) == 1
        assert rows[0].duration == 10.0


def test_different_activity_creates_new_row():
    with Storage(":memory:") as s:
        base = 1_000_000.0
        s.record("code", "a.py", "productive", False, 5.0, now=base + 5)
        s.record("firefox", "youtube", "unproductive", False, 5.0, now=base + 10)
        rows = s.all()
        assert len(rows) == 2


def test_query_window_filters():
    with Storage(":memory:") as s:
        s.record("code", "a.py", "productive", False, 5.0, now=100.0)
        s.record("code", "b.py", "productive", False, 5.0, now=10_000.0)
        assert len(s.query(0, 200)) == 1
        assert len(s.query(0, 20_000)) == 2
