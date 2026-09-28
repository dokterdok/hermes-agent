#!/usr/bin/env python3
"""Reconstruct the two published owners for the work-record admission regression."""

import argparse
import ast
from pathlib import Path
import subprocess
from urllib.request import urlopen

REPOSITORY = "https://github.com/dokterdok/hermes-agent.git"
PRESERVATION = "2135a0d9f2beaa58c6fe9fa34ad112d52f6185a9"
RETENTION = "c9f0029475f085e3b5e66b77df74cd5470925aef"


def public_blob(ref, path):
    url = f"https://raw.githubusercontent.com/dokterdok/hermes-agent/{ref}/{path}"
    with urlopen(url, timeout=60) as response:
        return response.read()


def transaction_node(source):
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == "_replica_transaction"]
    if len(nodes) != 1:
        raise ValueError("expected exactly one replica transaction provider")
    return nodes[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="absent directory inside an existing parent")
    destination = parser.parse_args().destination.absolute()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    destination.mkdir(mode=0o700)  # Refuse an existing destination, including symlinks.

    def git(*args):
        subprocess.run(["git", "-C", str(destination), *args], check=True)

    git("init", "--quiet")
    git("fetch", "--quiet", "--depth=1", REPOSITORY, PRESERVATION)
    git("checkout", "--quiet", "--detach", "FETCH_HEAD")
    actual = subprocess.check_output(
        ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
    if actual != PRESERVATION:
        raise ValueError("unexpected Preservation revision")

    path = destination / "gateway/hosted_room_replicas.py"
    consumer = path.read_text(encoding="utf-8")
    provider = public_blob(RETENTION, "gateway/hosted_room_replicas.py").decode("utf-8")
    old, new = transaction_node(consumer), transaction_node(provider)
    replacement = ast.get_source_segment(provider, new)
    if replacement is None:
        raise ValueError("missing provider source span")
    lines = consumer.splitlines(keepends=True)
    lines[old.lineno - 1:old.end_lineno] = [replacement + "\n"]
    path.write_text("".join(lines), encoding="utf-8")
    test = "tests/gateway/test_replica_transaction_authorization.py"
    (destination / test).write_bytes(public_blob(RETENTION, test))
    print(f"Reconstructed Preservation {PRESERVATION} + Retention {RETENTION}")
    print("Only the published transaction function and its test are composed; no tests have run.")


if __name__ == "__main__":
    main()
