#!/usr/bin/env bash
# usage: run.sh <tree> <script.py> [args...]
# Runs a live scenario script with the tree's own test-env interpreter, located through
# scripts/run-in-hermes-env. HERMES_DEV_HOME is an isolated dev home used only for that lookup; the
# script itself rebinds HERMES_HOME (and HOME/USERPROFILE/LOCALAPPDATA/APPDATA) to its scenario
# directory before it imports anything from the tree. PKG10_ROOT holds the checkouts
# (PKG10_ROOT/<tree>) and the run output (PKG10_ROOT/live).
set -u
tree="$1"; shift; script="$1"; shift
here=$(cd "$(dirname "$0")" && pwd)
: "${PKG10_ROOT:?set PKG10_ROOT to the folder holding the tree checkouts}"
export HERMES_HOME="${HERMES_DEV_HOME:?set HERMES_DEV_HOME to an isolated dev home}"
export HERMES_RUNTIME_DIR="$HERMES_HOME/tools"
cd "$PKG10_ROOT/$tree" || exit 2
exec scripts/run-in-hermes-env bash -c 'exec env -u PYTHONPATH "$__HERMES_TEST_PYTHON" "$@"' _ \
  "$here/$script" --tree "$tree" "$@"
