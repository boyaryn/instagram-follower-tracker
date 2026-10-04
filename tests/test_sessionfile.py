import os
import stat

import pytest

from igft import sessionfile

posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes only apply on POSIX")


class SimulatedCrash(BaseException):
    pass


@posix_only
def test_new_directory_is_0700_and_file_is_0600(tmp_path):
    path = tmp_path / "igft" / "sessions" / "instaloader.session"

    sessionfile.write_atomic(path, b"secret")

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sessionfile.read(path) == b"secret"


@posix_only
def test_existing_directory_keeps_its_mode(tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)

    sessionfile.write_atomic(directory / "s.session", b"x")

    assert stat.S_IMODE(directory.stat().st_mode) == 0o755


@posix_only
def test_overwrite_replaces_content_and_keeps_0600(tmp_path):
    path = tmp_path / "s.session"
    sessionfile.write_atomic(path, b"old")
    sessionfile.write_atomic(path, b"new")

    assert sessionfile.read(path) == b"new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["s.session"]


def test_crash_before_replace_leaves_old_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "s.session"
    sessionfile.write_atomic(path, b"old")

    def crash(src, dst):
        raise SimulatedCrash

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(SimulatedCrash):
        sessionfile.write_atomic(path, b"new")

    assert sessionfile.read(path) == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["s.session"]


def test_crash_while_writing_leaves_old_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "s.session"
    sessionfile.write_atomic(path, b"old")

    def crash(fd):
        raise SimulatedCrash

    monkeypatch.setattr(os, "fsync", crash)
    with pytest.raises(SimulatedCrash):
        sessionfile.write_atomic(path, b"new")

    assert sessionfile.read(path) == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["s.session"]


@posix_only
def test_loosened_permissions_produce_a_warning(tmp_path):
    path = tmp_path / "s.session"
    sessionfile.write_atomic(path, b"x")
    assert sessionfile.permission_warning(path) is None

    os.chmod(path, 0o644)
    warning = sessionfile.permission_warning(path)

    assert warning is not None
    assert "0644" in warning
    assert str(path) in warning
    assert "chmod 600" in warning


@posix_only
def test_group_writable_also_warns(tmp_path):
    path = tmp_path / "s.session"
    sessionfile.write_atomic(path, b"x")
    os.chmod(path, 0o660)

    assert sessionfile.permission_warning(path) is not None


def test_exists(tmp_path):
    path = tmp_path / "s.session"
    assert not sessionfile.exists(path)
    sessionfile.write_atomic(path, b"x")
    assert sessionfile.exists(path)
