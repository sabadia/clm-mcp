"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from clm_mcp import config

_CREDENTIAL_ENV_VARS = ("CLM_REFRESH_TOKEN", "CLM_USERNAME", "CLM_USER", "CLM_PASSWORD")


@pytest.fixture(autouse=True)
def _isolate_from_real_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip any real CLM credentials exported in the developer's shell, so
    no test silently picks them up (and none ever sends them to a live
    identity service). Tests that need credentials set them explicitly.

    Likewise keep the developer's real profiles file out of every test (it
    is pointed at a path that does not exist unless a test writes it), and
    send per-profile credential files to the test's temp dir.
    """
    for name in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLM_PROFILE", raising=False)
    monkeypatch.setenv("CLM_PROFILES_FILE", str(tmp_path / "profiles.toml"))
    monkeypatch.setattr(config, "DEFAULT_CREDENTIALS_PATH", tmp_path / "credentials.json")
