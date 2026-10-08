# Classic export owner composition (#104198)

This branch now contains the complete classic export producer, lifecycle and
exact-byte reader against the current runtime. Its dependent changes live in
ordinary source files; the former detached patch and replay recipe are retired.

The history preserves these reviewed dependency inputs:

| Dependency | Commit |
| --- | --- |
| Unified runtime #106742 | `86e81c44701e9ea43005c9b0d17aad90a36bb449` |
| Files #98072 | `31e2af969b9411835c108f1d3f22ef09b94cace5` |
| Hosted Output #99159, including cohosted transport correction | `2a92bda68730a03c12ec6f36b5f684cc65f5a297` |
| Transaction callbacks #111216 | `2103abb4a351cd1a48d6fc514d2823af2057b783` |

To reproduce a published owner revision in a disposable linked worktree:

```sh
scripts/compose_classic_owner.sh /absolute/path/to/classic-review HEAD
```

The destination must not exist. The script verifies dependency ancestry and
checks out the exact requested owner commit. It neither fetches moving PR refs
nor substitutes a lower layer's tree. Remove the linked checkout with
`git worktree remove /absolute/path/to/classic-review` after review.

Prepare a disposable test environment using the repository's current package
manager, as described in `CONTRIBUTING.md`, and point `HERMES_PYTHON` at its
interpreter when it is outside the checkout. Run the canonical hermetic wrapper:

```sh
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh -j 2 \
  tests/gateway/test_classic_current_export.py \
  tests/gateway/test_classic_export_read.py \
  tests/gateway/test_classic_retirement_boundaries.py \
  tests/gateway/test_classic_cleanup_windows.py
```

The cases execute real canonical admission, registered `share_group_file`,
terminal settlement, exact retained-byte reads, scope/generation refusals and
retryable cleanup against temporary state. They use controlled provider and
transport boundaries; hosted CI and a full native Desktop journey are separate.
POSIX symlink cases run on POSIX. Native Windows cases cover pinned-handle
removal, directory flush, replay after removal, and junction refusal without
requiring symbolic-link privileges.

Classic cleanup borrows only the current owner's transaction and calls the
existing outbox generation fence directly. It does not construct an outbox,
reopen a database, reconcile schema, or run unrelated expiry maintenance during
retirement. The retirement intent commits before physical cleanup. A filesystem
failure retains its exact retry obligation. The reader remains non-creating and
supports only already-bound default-profile producers and proven compression
lineage; named profiles and other execution surfaces remain unavailable.
