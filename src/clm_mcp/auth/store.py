"""On-disk credential storage for `clm-mcp login`.

Stores only a refresh token (never a password) as JSON at a
user-configurable path, defaulting to `~/.config/clm-mcp/credentials.json`.
The file and its parent directory are created with restrictive permissions
(0600 / 0700) since it is a bearer credential.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from clm_mcp.auth.models import StoredCredentials
from clm_mcp.logging import get_logger

logger = get_logger(__name__)

_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0o600
_DIR_MODE = stat.S_IRWXU  # 0o700


def load_credentials(path: Path) -> StoredCredentials | None:
    """Load stored credentials from `path`, or return None if absent/invalid.

    A missing file is the expected common case (no error logged). A present
    but unreadable/malformed file is logged as a warning and treated as
    absent, so callers fall through to the next credential source rather
    than crashing on a corrupted cache file.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        return StoredCredentials.model_validate_json(raw)
    except (OSError, ValidationError) as exc:
        logger.warning("credentials_store.load_failed", path=str(path), error=str(exc))
        return None


def save_credentials(path: Path, refresh_token: str, username: str | None = None) -> None:
    """Persist a refresh token to `path` with 0600 permissions.

    Creates the parent directory (0700) if needed. Writes to a temp file in
    the same directory and atomically renames into place, so a crash
    mid-write never leaves a corrupted or partially-readable credentials
    file behind.

    Note: `StoredCredentials.model_dump_json()` is deliberately NOT used
    here — pydantic's `SecretStr` masks its value on JSON dump (correct for
    logs, wrong for a store we need to read the real token back from), so
    the payload is serialized explicitly instead.
    """
    creds = StoredCredentials(refresh_token=refresh_token, username=username)  # validates shape

    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, _DIR_MODE)

    payload = json.dumps(
        {
            "refresh_token": refresh_token,
            "saved_at": creds.saved_at.isoformat(),
            "username": username,
        }
    )

    tmp_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.chmod(tmp_path, _FILE_MODE)
    tmp_path.replace(path)
    logger.info("credentials_store.saved", path=str(path))
