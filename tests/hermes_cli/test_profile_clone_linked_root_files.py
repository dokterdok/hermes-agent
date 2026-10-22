"""``--clone-all`` copies a symlinked root file the way the SOURCE profile reads it (#142943).

Clones are staged under ``profiles/.profile-creating/<attempt>/<name>``, deeper than any source, so a
relative link resolved from the staged copy names another path: the clone silently lost its keys,
config or persona. Each case links one root file the clone edits, relative to the source's folder.
"""

import os
from pathlib import Path

import pytest

from hermes_cli import profiles

_LINKED = {".env": "OPENROUTER_API_KEY=fixture\nTELEGRAM_BOT_TOKEN=1:fixture\n",
           "config.yaml": "model:\n  default: rv-model\n", "SOUL.md": "The source's soul.\n"}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda *_: None)
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda *_: None)
    return tmp_path / ".hermes"


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("name", sorted(_LINKED))
@pytest.mark.parametrize("layout", ["named", "default", "linked-source-dir", "default-into-excluded-bin"])
def test_clone_all_copies_what_the_source_reads_through_a_relative_link(root, tmp_path, layout, name):
    default = layout.startswith("default")
    src = root if default else root / "profiles" / "src"
    if layout == "linked-source-dir":  # profiles/src -> elsewhere/src: `..` is the physical parent
        (root / "profiles").mkdir(parents=True)
        src.symlink_to(tmp_path / "elsewhere" / "src", target_is_directory=True)
    real = Path(os.path.realpath(src))
    real.mkdir(parents=True, exist_ok=True)
    (src / "profile.yaml").write_text("description: fixture\n")
    target = real / "bin" / name if layout.endswith("bin") else real.parent / f"shared-{name.lstrip('.')}"
    target.parent.mkdir(exist_ok=True)
    target.write_text(_LINKED[name])
    (src / name).symlink_to(os.path.relpath(target, real))

    clone = profiles.create_profile("cloned", clone_from="default" if default else "src",
                                    clone_all=True, no_alias=True)  # the channel strip edits the clone

    text = (clone / name).read_text()
    assert not (clone / name).is_symlink() and _LINKED[name].splitlines()[0] in text
    assert "TELEGRAM_BOT_TOKEN" not in text and target.read_text() == _LINKED[name]


@pytest.mark.platforms("posix")
def test_clone_all_still_drops_a_link_that_dangles_in_the_source(root):
    (root / "profiles" / "src").mkdir(parents=True)
    (root / "profiles" / "src" / ".env").symlink_to(Path("..") / "missing.env")
    clone = profiles.create_profile("cloned", clone_from="src", clone_all=True, no_alias=True)
    assert not (clone / ".env").is_symlink() and not (root / "profiles" / "missing.env").exists()
    assert (clone / ".env").read_text() == profiles._PLACEHOLDER_ENV
