"""Owner-private, symlink-safe filesystem helpers (LAB-040, LAB-070)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from .errors import LabError


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) as 0700 directories owned by us.

    Refuses symlinks and foreign ownership for the final component; tightens
    permissions of an existing directory that we own.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise LabError("unsafe_configuration", "lab directory is not a plain directory", {"path_kind": "lab-dir"})
    if st.st_uid != os.getuid():
        raise LabError("unsafe_configuration", "lab directory is owned by another user", {"path_kind": "lab-dir"})
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def atomic_write_json(path: Path, obj, mode: int = 0o600) -> None:
    path = Path(path)
    data = json.dumps(obj, indent=2, sort_keys=True).encode() + b"\n"
    atomic_write_bytes(path, data, mode)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json_nofollow(path: Path, max_bytes: int = 1 << 20):
    """Read a JSON file without following a symlink in the final component."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise LabError("unsafe_configuration", "state file exceeds size bound", {"max_bytes": max_bytes})
    return json.loads(data)


def remove_tree(path: Path) -> None:
    """Remove a lab-owned tree without following symlinks out of it."""
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        os.unlink(path)
        return
    for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            os.unlink(os.path.join(root, name))
        for name in dirs:
            full = os.path.join(root, name)
            if os.path.islink(full):
                os.unlink(full)
            else:
                os.rmdir(full)
    os.rmdir(path)


def tree_size(path: Path) -> int:
    """Apparent allocated size (st_blocks * 512) of a tree, not following links."""
    total = 0
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_blocks * 512
    seen: set[tuple[int, int]] = set()
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            try:
                s = os.lstat(os.path.join(root, name))
            except FileNotFoundError:
                continue
            key = (s.st_dev, s.st_ino)
            if key in seen:
                continue
            seen.add(key)
            total += s.st_blocks * 512
    return total + st.st_blocks * 512
