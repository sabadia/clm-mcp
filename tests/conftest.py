"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

_CREDENTIAL_ENV_VARS = ("CLM_REFRESH_TOKEN", "CLM_USERNAME", "CLM_USER", "CLM_PASSWORD")


@pytest.fixture(autouse=True)
def _isolate_from_real_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any real CLM credentials exported in the developer's shell, so
    no test silently picks them up (and none ever sends them to a live
    identity service). Tests that need credentials set them explicitly."""
    for name in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
