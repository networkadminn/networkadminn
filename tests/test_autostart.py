import sys

import pytest

from timetrack.agent import autostart


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG autostart")
def test_linux_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(autostart, "_system_desktop", lambda: tmp_path / "missing.desktop")
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
    assert "Hidden=true" in (tmp_path / "autostart" / autostart.DESKTOP_ID).read_text()
