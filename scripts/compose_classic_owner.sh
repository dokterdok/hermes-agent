#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'usage: %s DESTINATION [OWNER_REV]\n' "$0" >&2
    exit 2
}
[[ $# -ge 1 && $# -le 2 ]] || usage
OWNER_ROOT=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
OWNER_REV=${2:-HEAD}
OWNER_SHA=$(git -C "$OWNER_ROOT" rev-parse "${OWNER_REV}^{commit}")
DEST=$1
case "$DEST" in
    /* | [A-Za-z]:[\\/]*) ;;
    *) DEST="$PWD/$DEST" ;;
esac
[[ ! -e "$DEST" ]] || { printf 'destination already exists: %s\n' "$DEST" >&2; exit 2; }

# The owner is now a complete, history-preserving composition. Verify its
# prerequisites, then reproduce that exact tree without replaying old patches.
for dependency in \
    b4971df21a165dfa7dc32739b9d13dd2f37b103c \
    b110300eb91d86d13e9735c00d3e71d23d000000 \
    d615b6f8035a2a8b5c3adbf99e6512a484d10b44 \
    985a366f69309f2b51b1f22cc371b5fd9a6cd8f8; do
    git -C "$OWNER_ROOT" merge-base --is-ancestor "$dependency" "$OWNER_SHA" || {
        printf 'owner does not contain required dependency: %s\n' "$dependency" >&2
        exit 1
    }
done
git -C "$OWNER_ROOT" worktree add --detach "$DEST" "$OWNER_SHA"
printf 'owner=%s\ntree=%s\n' "$OWNER_SHA" "$(git -C "$DEST" rev-parse 'HEAD^{tree}')"
