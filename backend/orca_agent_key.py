"""Validate CloakBrowser Manager agent key files without echoing secrets."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

DEFAULT_AGENT_KEY_FILE = "/home/coder/.config/cloakbrowser/orca-agent-key"
# Prefix + at least 16 safe token characters (opaque key material).
_AGENT_KEY_RE = re.compile(r"^cbm_agent_[A-Za-z0-9_-]{16,}$")


def check_agent_key_file(path: str | Path | None = None) -> list[str]:
    """Return human-safe error notes; never include key contents."""
    candidate = Path(
        str(path or os.environ.get("CBM_AGENT_KEY_FILE") or DEFAULT_AGENT_KEY_FILE)
    ).expanduser()
    errors: list[str] = []

    try:
        st = candidate.lstat()
    except FileNotFoundError:
        return ["agent key file is missing"]
    except OSError:
        return ["agent key file is not readable"]

    if stat.S_ISLNK(st.st_mode):
        return ["agent key file must be a regular file (symlink refused)"]
    if not stat.S_ISREG(st.st_mode):
        return ["agent key file must be a regular file"]

    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        errors.append("agent key file mode must be exactly 0600")

    try:
        if st.st_uid != os.getuid():
            errors.append("agent key file must be owned by the current host user")
    except AttributeError:
        # Platforms without getuid still require readability below.
        pass

    if not os.access(candidate, os.R_OK):
        errors.append("agent key file is not readable by the current host user")

    if errors:
        return errors

    try:
        raw = candidate.read_text(encoding="utf-8")
    except OSError:
        return ["agent key file is not readable"]
    key = raw.strip()
    if not key:
        return ["agent key file is empty"]
    if not _AGENT_KEY_RE.fullmatch(key):
        return ["agent key file failed syntactic validation"]
    return []
