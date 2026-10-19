#!/usr/bin/env bash
# usage: run.sh <tree dir> <out dir> <script.py> [args...]
# Runs a check with the tree's own test-env interpreter, located through scripts/run-in-hermes-env.
# HERMES_DEV_HOME is an isolated dev home used only for that lookup; the script rebinds HERMES_HOME
# (and HOME/USERPROFILE/LOCALAPPDATA/APPDATA) to its run directory before it imports the tree.
set -u
tree=$(cd "$1" && pwd); out="$2"; script="$3"; shift 3
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"; out=$(cd "$out" && pwd)
export HERMES_HOME="${HERMES_DEV_HOME:?set HERMES_DEV_HOME to an isolated dev home}"
export HERMES_RUNTIME_DIR="$HERMES_HOME/tools"
cd "$tree" || exit 2
exec scripts/run-in-hermes-env bash -c 'exec env -u PYTHONPATH "$__HERMES_TEST_PYTHON" "$@"' _ \
  "$here/$script" --root "$tree" --out "$out" "$@"
