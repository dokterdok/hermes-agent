"""Execute dependency-free Desktop behavior probes through the canonical runner."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("case", ["unseen_profile_total", "partial_overlap_total", "native_archive", "legacy_pin_prefix", "archived_pin_gap", "later_errors"])
def test_desktop_delta_contract(case):
    root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("delta_contracts.mjs")), case],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
