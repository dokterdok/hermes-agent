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
    86e81c44701e9ea43005c9b0d17aad90a36bb449 \
    31e2af969b9411835c108f1d3f22ef09b94cace5 \
    2a92bda68730a03c12ec6f36b5f684cc65f5a297 \
    2103abb4a351cd1a48d6fc514d2823af2057b783; do
    git -C "$OWNER_ROOT" merge-base --is-ancestor "$dependency" "$OWNER_SHA" || {
        printf 'owner does not contain required dependency: %s\n' "$dependency" >&2
        exit 1
    }
done
git -C "$OWNER_ROOT" worktree add --detach "$DEST" "$OWNER_SHA"
printf 'owner=%s\ntree=%s\n' "$OWNER_SHA" "$(git -C "$DEST" rev-parse 'HEAD^{tree}')"
