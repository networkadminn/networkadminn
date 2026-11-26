"""Agent config path resolution (Windows daily-login fix)."""

from __future__ import annotations

from pathlib import Path

import pytest

from timetrack.agent import config as agent_config


def test_prefers_profile_token_over_empty_cwd(tmp_path, monkeypatch):
    install = tmp_path / "install"
    profile = tmp_path / "profile"
    install.mkdir()
    profile.mkdir()
    (install / "agent.toml").write_text(
        '[agent]\nserver_url = "https://tracker.example"\napi_token = ""\n',
        encoding="utf-8",
    )
    (profile / "agent.toml").write_text(
        '[agent]\nserver_url = "https://tracker.example"\napi_token = "saved-token"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", profile / "agent.toml")
    monkeypatch.chdir(install)

    cfg = agent_config.load_agent_config()
    assert cfg.api_token == "saved-token"
    assert Path(cfg.config_path) == profile / "agent.toml"


def test_save_sign_in_goes_to_profile(tmp_path, monkeypatch):
    profile = tmp_path / "profile" / "agent.toml"
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", profile)
    cfg = agent_config.AgentConfig(
        server_url="https://tracker.example",
        api_token="new-token",
        config_path=str(tmp_path / "install" / "agent.toml"),
    )
    dest = agent_config.save_agent_config(cfg)
    assert dest == profile
    assert "new-token" in profile.read_text(encoding="utf-8")


def test_windows_migrates_token_from_install_dir_to_profile(tmp_path, monkeypatch):
    install = tmp_path / "install"
    profile = tmp_path / "profile"
    install.mkdir()
    profile.mkdir(parents=True)
    install_file = install / "agent.toml"
    install_file.write_text(
        '[agent]\nserver_url = "https://tracker.example"\napi_token = "migrate-me"\n',
        encoding="utf-8",
    )
    profile_file = profile / "agent.toml"
    monkeypatch.setattr(agent_config, "USER_CONFIG_PATH", profile_file)
    monkeypatch.setattr(agent_config.sys, "platform", "win32")
    monkeypatch.chdir(install)

    cfg = agent_config.load_agent_config()
    assert cfg.api_token == "migrate-me"
    assert profile_file.is_file()
    assert "migrate-me" in profile_file.read_text(encoding="utf-8")
