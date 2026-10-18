"""Targeted existing JS suites, invoked only by scripts/run_tests.sh."""
import os
from pathlib import Path
import subprocess


def test_existing_desktop_suites():
    root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env["NODE_ENV"] = "development"
    result = subprocess.run(
        ["node", str(root / "node_modules/vitest/vitest.mjs"), "run",
         "electron/profile-session-routing.test.ts",
         "src/store/sidebar-archive.test.ts",
         "src/plugins/hermes-bots/canonical-group-reopen-by-name.test.tsx",
         "src/plugins/hermes-bots/canonical-group-capability-gates.test.tsx",
         "--maxWorkers", "1", "--no-file-parallelism"],
        cwd=root / "apps/desktop", env=env, capture_output=True, text=True, timeout=240,
    )
    print(result.stdout)
    print(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
