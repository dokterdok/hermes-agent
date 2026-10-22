"""A root-run Hermes (``sudo hermes -p <name> ...``) hands every lifecycle entry it creates in another
uid's tree to that tree's owner: a root:root 0700 ``profiles/.locks`` alone locks the owner's gateway
and CLI out of every named profile's state.db.

Seams (the suite never runs as root): ``os.geteuid()`` is 0; ``os.fstat`` reports uid/gid 0 for an
entry created during the root-run step, as under sudo; ``os.fchown`` records what is handed back.
"""
import os
import shutil
from pathlib import Path

import pytest

from hermes_cli import config_home, profiles
from hermes_cli.profile_lifecycle import mark_profile_deleting, profile_lifecycle_lease, profile_selection_lease
from hermes_state import SessionDB

pytestmark = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="needs a non-root owner")


class _RootOwned:
    def __init__(self, st): self._st = st
    def __getattr__(self, key): return 0 if key in ("st_uid", "st_gid") else getattr(self._st, key)


def _become_root(monkeypatch, tree: Path) -> tuple[set, set]:
    """*tree* as it stands is the owner's; anything created from here on is root's until handed back."""
    before, handed, real_fstat = {p.lstat().st_ino for p in (tree, *tree.rglob("*"))}, set(), os.fstat
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fstat", lambda fd: st if (st := real_fstat(fd)).st_ino in before | handed
                        else _RootOwned(st))
    monkeypatch.setattr(os, "fchown", lambda fd, *_: handed.add(real_fstat(fd).st_ino))
    return before, handed


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A legacy profile, as an older release left it: no marker, no lifecycle directories."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda *_: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda *_: None)
    home = profiles.create_profile("work", no_alias=True, no_skills=True)
    (home / ".profile-incarnation").unlink()
    for leftover in (".locks", ".deleted", ".profile-creating"):
        shutil.rmtree(home.parent / leftover)
    return home


@pytest.mark.platforms("posix")
def test_root_run_leaves_no_root_owned_lifecycle_entry(work, monkeypatch):
    before, handed = _become_root(monkeypatch, work.parent)
    config_home.ensure_home(work, ("cron", "sessions", "logs", "memories"), {})  # sudo hermes -p work chat
    SessionDB(db_path=work / "state.db").close()
    profiles.create_profile("other", no_alias=True, no_skills=True)  # root's own profile stays root's
    mark_profile_deleting(work, "0" * 32)                             # sudo hermes profile delete work
    lifecycle = {".locks", ".deleted", ".profile-creating", ".profile-incarnation"}
    created = [p for p in work.parent.rglob("*") if p.lstat().st_ino not in before
               and lifecycle & set(rel := p.relative_to(work.parent).parts) and rel[0] != "other"]
    assert len(created) >= 6 and all(p.lstat().st_ino in handed for p in created), sorted(map(str, created))


@pytest.mark.platforms("posix")
def test_root_hands_back_a_profiles_dir_it_creates(tmp_path, monkeypatch):
    (root := tmp_path / ".hermes").mkdir()  # no named profile yet
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(root))
    _before, handed = _become_root(monkeypatch, root)
    with profile_selection_lease():  # sudo hermes profile use default
        pass
    created = [root / "profiles", *(root / "profiles").rglob("*")]
    assert len(created) == 3 and all(p.lstat().st_ino in handed for p in created), sorted(map(str, created))


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("plant", ["symlink", "hardlink"])
def test_root_never_hands_back_a_file_planted_at_a_lock_name(work, tmp_path, monkeypatch, plant):
    (locks := work.parent / ".locks").mkdir(mode=0o700)
    _before, handed = _become_root(monkeypatch, work.parent)
    (foreign := tmp_path / "root-owned").write_text("")  # e.g. /etc/sudoers, linked in by the tree's owner
    (os.symlink if plant == "symlink" else os.link)(foreign, locks / "work.lock")
    with profile_lifecycle_lease(work):
        pass
    assert foreign.lstat().st_ino not in handed
