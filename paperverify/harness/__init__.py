"""Harness profiles for agent frontends.

These profiles describe how paper-verify should be exposed to each agent
frontend without forking the core verification pipeline.
"""

from .base import HarnessProfile, get_profile, list_profiles

__all__ = ["HarnessProfile", "get_profile", "list_profiles"]

