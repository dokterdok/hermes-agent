"""Assembly candidate stays on the cb8 blobs for backend files Barry still owns.

Pin parentage for route 8a29b6d and retention c9f0029 is recorded in
docs/layers-integration/BACKEND_CONFLICTS.md. Those objects are not ancestors of
this branch, so a clone of the branch cannot resolve them.
"""

import subprocess

import pytest

CB8 = "cb8d6920549ebe9d31f69f187d30b356b5639eed"
ROUTE = "8a29b6d13bc558221130b4216a93f373afa68caf"
ROUTE_SUPERSEDED = "1fa3c0addd0c3eec671f3019c443dd3e449db134"
RETENTION_F1 = "c9f0029475f085e3b5e66b77df74cd5470925aef"
RETENTION_RECIPE = "004015d6087fe031231c4d7d9e0032cc59b679eb"

# Blobs of the assembly target. Changing one is a backend edit, not a client commit.
UNTOUCHED = {
    "gateway/session_policy.py": "5aa15e8e8fc19d31b7156e93515d59aab94fb52c",
    "hermes_state_runtime.py": "48960a91fb2c669bd2aa311960381ca7a4415dc6",
    "gateway/hosted_rooms.py": "d959f06ebc22d9c6ad5b459a81dbc4cab9192bde",
    "gateway/hosted_room_replicas.py": "505404179ccc1ff50182b9a1707f35ef7bfb9a50",
    "agent/runtime_session_store.py": "1392ff1056fcebbc9861d9ef7053777552cd18bb",
}


def _rev_parse(spec: str) -> str:
    return subprocess.check_output(["git", "rev-parse", spec], text=True).strip()


def _has_object(sha: str) -> bool:
    completed = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], check=False)
    return completed.returncode == 0


def test_assembly_head_contains_the_cb8_pin():
    subprocess.check_call(["git", "merge-base", "--is-ancestor", CB8, "HEAD"])


@pytest.mark.parametrize("path,blob", UNTOUCHED.items())
def test_owned_backend_blobs_stay_on_cb8(path, blob):
    assert _rev_parse(f"HEAD:{path}") == blob


def test_route_pin_parent_is_the_superseded_snapshot():
    if not _has_object(ROUTE):
        pytest.skip("route pin is not in this clone")
    assert _rev_parse(f"{ROUTE}^") == ROUTE_SUPERSEDED


def test_retention_f1_parent_is_the_recipe_pin():
    if not _has_object(RETENTION_F1):
        pytest.skip("retention pin is not in this clone")
    assert _rev_parse(f"{RETENTION_F1}^") == RETENTION_RECIPE
