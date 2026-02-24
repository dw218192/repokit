"""Feature-based executable helpers.

``find_executable`` is stdlib-only and safe for lightweight imports
(e.g. the stdio MCP lint server).  ``require_executable`` adds
user-friendly error messages that reference which feature to enable.
"""

from __future__ import annotations

import shutil
import sys
from functools import cache
from pathlib import Path


@cache
def find_executable(name: str) -> str | None:
    """Find *name* in the venv Scripts dir or system PATH.

    Returns the path string, or ``None`` if not found.
    """
    scripts_dir = Path(sys.executable).parent
    suffix = ".exe" if sys.platform == "win32" else ""
    exe_path = scripts_dir / (name + suffix)
    if exe_path.exists():
        return str(exe_path)
    return shutil.which(name)


def require_executable(name: str, *, feature: str) -> str:
    """Find *name* or exit with a helpful error about which feature to enable.

    Returns the executable path on success.  On failure calls
    ``sys.exit(1)`` after logging a message that tells the user
    which *feature* group to add to ``repo.features`` in config.yaml.
    """
    exe = find_executable(name)
    if exe is not None:
        return exe

    from .core import logger  # lazy — keep module-level stdlib-only

    logger.error(
        "%s not found. Enable the '%s' feature in config.yaml "
        "(repo.features) and run 'repo init' to install it.",
        name,
        feature,
    )
    sys.exit(1)
