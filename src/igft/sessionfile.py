"""The saved session file (design D6): owner-only permissions and atomic writes.

This module only moves bytes. It never imports instaloader, so commands that make no Instagram
request can use it.
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600


def exists(path: Path) -> bool:
    return path.is_file()


def write_atomic(path: Path, data: bytes) -> None:
    """Replace the session file with `data`, so a crash never leaves a half-written file behind.

    The data goes to a `0600` temporary file in the same directory first and is moved over the old
    file with `os.replace`. A directory this call creates gets mode `0700`; an existing directory
    is left as it is.
    """
    directory = path.parent
    if not directory.exists():
        directory.mkdir(mode=DIR_MODE, parents=True)
        if os.name == "posix":
            os.chmod(directory, DIR_MODE)
    temp = directory / f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def read(path: Path) -> bytes:
    return path.read_bytes()


def permission_warning(path: Path) -> str | None:
    """A warning when other users can read or write the session file, else None.

    File modes only mean something on POSIX systems, so nothing is checked elsewhere.
    """
    if os.name != "posix":
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077 == 0:
        return None
    return (
        f"Warning: the session file {path} can be accessed by other users (mode {mode:04o}). "
        f"Restrict it with `chmod {FILE_MODE:o} {path}`."
    )
