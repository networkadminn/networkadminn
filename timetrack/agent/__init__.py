"""TimeTrack employee agent: tracks activity + screenshots and syncs to a server."""

from .agent import Agent
from .config import AgentConfig, load_agent_config

__all__ = ["Agent", "AgentConfig", "load_agent_config"]
