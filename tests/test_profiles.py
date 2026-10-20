"""Tests for named configuration profiles (`profiles.toml`): selection
precedence, credential isolation, env-var indirection, and validation."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from clm_mcp import config
from clm_mcp.config import ProfileError, Settings, load_settings

PROD = """
[profiles.prod]
identity_token_url = "https://prod.test/api/identity/v25/identity/token"
identity_origin = "https://prod.test/"
api_root_url = "https://api.prod.test/api"
username_env = "CLM_PROD_USER"
password_env = "CLM_PROD_PASSWORD"
enable_writes = false
"""

STAGE = """
[profiles.stage]
api_root_url = "https://api.stage.test/api"
username = "stage@example.com"
password_env = "CLM_STAGE_PASSWORD"
"""


WriteProfiles = Callable[[str], Path]


@pytest.fixture
def profiles_file() -> WriteProfiles:
    """Write the (per-test, see conftest.py) CLM_PROFILES_FILE."""
    path = Path(os.environ["CLM_PROFILES_FILE"])

    def write(text: str) -> Path:
        path.write_text(text)
        return path

    return write


def test_no_profile_and_no_file_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLM_USER", "plain@example.com")
    settings = load_settings()
    assert settings.profile is None
    assert settings.username == "plain@example.com"
    assert settings.api_root_url == "https://msblocks.selisestage.com/api"
    assert settings.credentials_path == Settings.model_fields["credentials_path"].default


def test_profile_supplies_urls_and_reads_credentials_from_named_env_vars(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file(PROD)
    monkeypatch.setenv("CLM_PROD_USER", "prod@example.com")
    monkeypatch.setenv("CLM_PROD_PASSWORD", "prod-secret")

    settings = load_settings("prod")

    assert settings.profile == "prod"
    assert settings.identity_token_url == "https://prod.test/api/identity/v25/identity/token"
    assert settings.identity_origin == "https://prod.test/"
    assert settings.api_root_url == "https://api.prod.test/api"
    assert settings.username == "prod@example.com"
    assert settings.password is not None
    assert settings.password.get_secret_value() == "prod-secret"
    assert settings.enable_writes is False


def test_profile_never_inherits_global_credentials(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile that names no credentials must NOT fall back to the global
    CLM_USER/CLM_PASSWORD/CLM_REFRESH_TOKEN — those may belong to another
    environment entirely."""
    profiles_file('[profiles.bare]\napi_root_url = "https://api.bare.test/api"\n')
    monkeypatch.setenv("CLM_USER", "global@example.com")
    monkeypatch.setenv("CLM_PASSWORD", "global-secret")
    monkeypatch.setenv("CLM_REFRESH_TOKEN", "global-rt")

    settings = load_settings("bare")

    assert settings.username is None
    assert settings.password is None
    assert settings.refresh_token is None


def test_profile_inherits_non_credential_settings_from_env(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file(STAGE)
    monkeypatch.setenv("CLM_STAGE_PASSWORD", "x")
    monkeypatch.setenv("CLM_LOG_LEVEL", "DEBUG")
    settings = load_settings("stage")
    assert settings.log_level == "DEBUG"
    # ...and unset URLs fall back to the built-in defaults.
    assert settings.identity_origin == "https://clm.selisestage.com/"


def test_profile_value_wins_over_global_env_for_same_setting(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file(PROD)
    monkeypatch.setenv("CLM_PROD_USER", "u")
    monkeypatch.setenv("CLM_PROD_PASSWORD", "p")
    monkeypatch.setenv("CLM_API_ROOT_URL", "https://global.test/api")
    assert load_settings("prod").api_root_url == "https://api.prod.test/api"


def test_each_profile_gets_its_own_credentials_file(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file(PROD)
    monkeypatch.setenv("CLM_PROD_USER", "u")
    monkeypatch.setenv("CLM_PROD_PASSWORD", "p")
    assert load_settings("prod").credentials_path == (
        config.DEFAULT_CREDENTIALS_PATH.with_name("credentials-prod.json")
    )


def test_explicit_credentials_path_is_honoured_and_tilde_expanded(
    profiles_file: WriteProfiles,
) -> None:
    profiles_file('[profiles.x]\ncredentials_path = "~/clm-x.json"\n')
    assert load_settings("x").credentials_path == Path.home() / "clm-x.json"


def test_default_key_selects_profile(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file('default = "stage"\n' + PROD + STAGE)
    monkeypatch.setenv("CLM_STAGE_PASSWORD", "x")
    assert load_settings().profile == "stage"


def test_clm_profile_env_overrides_default_key(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file('default = "stage"\n' + PROD + STAGE)
    monkeypatch.setenv("CLM_PROD_USER", "u")
    monkeypatch.setenv("CLM_PROD_PASSWORD", "p")
    monkeypatch.setenv("CLM_PROFILE", "prod")
    assert load_settings().profile == "prod"


def test_explicit_argument_overrides_clm_profile_env(
    profiles_file: WriteProfiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles_file(PROD + STAGE)
    monkeypatch.setenv("CLM_STAGE_PASSWORD", "x")
    monkeypatch.setenv("CLM_PROFILE", "prod")
    assert load_settings("stage").profile == "stage"


def test_unknown_profile_lists_available_ones(profiles_file: WriteProfiles) -> None:
    profiles_file(PROD + STAGE)
    with pytest.raises(ProfileError, match=r"'nope'.*\['prod', 'stage'\]"):
        load_settings("nope")


def test_profile_requested_but_file_missing() -> None:
    with pytest.raises(ProfileError, match="profiles file"):
        load_settings("prod")


def test_referenced_env_var_that_is_unset_is_an_error(profiles_file: WriteProfiles) -> None:
    profiles_file(PROD)
    with pytest.raises(ProfileError, match="CLM_PROD_USER"):
        load_settings("prod")


@pytest.mark.parametrize("key", ["password", "refresh_token"])
def test_literal_secret_in_file_is_rejected(profiles_file: WriteProfiles, key: str) -> None:
    profiles_file(f'[profiles.x]\n{key} = "oops"\n')
    with pytest.raises(ProfileError, match=f"{key}_env"):
        load_settings("x")


def test_unknown_key_is_rejected(profiles_file: WriteProfiles) -> None:
    profiles_file('[profiles.x]\napi_rot_url = "typo"\n')
    with pytest.raises(ProfileError, match="api_rot_url"):
        load_settings("x")


def test_invalid_toml_is_a_profile_error(profiles_file: WriteProfiles) -> None:
    profiles_file("[profiles.x\n")
    with pytest.raises(ProfileError, match="profiles file"):
        load_settings("x")
