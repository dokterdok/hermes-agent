"""Real renderer + Electron aggregation integration probe, via the canonical runner."""
import os
from pathlib import Path
import subprocess


def test_real_archive_loader():
    root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env["NODE_ENV"] = "development"
    result = subprocess.run(
        ["node", str(root / "node_modules/vitest/vitest.mjs"), "run", "--config",
         str(Path(__file__).with_name("vitest.config.ts")), "--maxWorkers", "1", "--no-file-parallelism"],
        cwd=root / "apps/desktop", env=env, capture_output=True, text=True, timeout=240,
    )
    Path(__file__).with_name("real-archive-vitest-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
