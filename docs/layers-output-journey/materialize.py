#!/usr/bin/env python3
"""Reconstruct the pinned Layers composition from public Git objects and explicit joins.

No fetched Python is imported or executed. Destination must be absent. A failed
run leaves its isolated destination for inspection rather than deleting it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

HERE = Path(__file__).resolve().parent
PIN = re.compile(r"[0-9a-f]{40}\Z")
REMOTE = "https://github.com/dokterdok/hermes-agent.git"
MAX_BLOB = 8 * 1024 * 1024


def run(*args, cwd=None):
    proc = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {args[:4]!r}: "
                           f"{proc.stderr.decode(errors='replace')[-1200:]}")
    return proc.stdout


def git(repo, *args):
    return run('git', '-C', str(repo), *args)


def safe_path(raw):
    if not isinstance(raw, str) or not raw or raw.startswith('/') or '\\' in raw or ':' in raw:
        raise ValueError(f'unsafe path: {raw!r}')
    parts = PurePosixPath(raw).parts
    if (not parts or '.git' in parts or any(part in ('', '.', '..') for part in raw.split('/'))
            or any(ord(ch) < 32 for ch in raw)):
        raise ValueError(f'unsafe path: {raw!r}')
    return parts


def read_plan(path):
    plan = json.loads(path.read_text('utf-8'))
    if plan.get('format') != 1 or plan.get('remote') != REMOTE or plan.get('base') != 'base':
        raise ValueError('unknown composition format, remote, or base')
    sources = plan['sources']
    if set(sources) != {'base', 'runtime', 'runtime-base', 'output', 'output-base',
                        'route', 'route-base', 'input', 'files', 'retention'}:
        raise ValueError('source set drift')
    if any(not isinstance(pin, str) or not PIN.fullmatch(pin) for pin in sources.values()):
        raise ValueError('all sources require full immutable commit pins')
    files = plan['files']
    if not isinstance(files, list) or not files:
        raise ValueError('empty composition')
    seen = set()
    for entry in files:
        path = entry['path']
        safe_path(path)
        if path in seen or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise ValueError(f'duplicate path or invalid digest: {path}')
        seen.add(path)
        if entry['mode'] not in ('100644', '100755') or not entry['parts']:
            raise ValueError(f'invalid mode or empty parts: {path}')
        for part in entry['parts']:
            if ('source' in part) == ('join' in part):
                raise ValueError(f'part must name exactly one operation: {path}')
            if 'source' in part:
                if part['source'] not in sources or not PIN.fullmatch(part['blob']):
                    raise ValueError(f'invalid source: {path}')
                if 'lines' in part:
                    pair = part['lines']
                    if (not isinstance(pair, list) or len(pair) != 2
                            or any(type(n) is not int for n in pair)
                            or pair[0] < 1 or pair[1] < pair[0]):
                        raise ValueError(f'invalid source span: {path}')
            elif (not isinstance(part['join'], str) or not part['join']
                  or not isinstance(part.get('reason'), str) or not part['reason']):
                raise ValueError(f'unexplained/empty mechanical join: {path}')
    return plan


def checked_blob(repo, pin, path, expected, cache):
    key = (pin, path)
    if key not in cache:
        identity = git(repo, 'rev-parse', f'{pin}:{path}').decode().strip()
        if identity != expected:
            raise RuntimeError(f'public blob identity drift: {pin}:{path}')
        kind = git(repo, 'cat-file', '-t', identity).decode().strip()
        if kind != 'blob':
            raise RuntimeError(f'non-blob source: {path}')
        size = int(git(repo, 'cat-file', '-s', identity))
        if size > MAX_BLOB:
            raise RuntimeError(f'oversized source: {path}')
        cache[key] = git(repo, 'cat-file', 'blob', identity)
        if len(cache[key]) != size:
            raise RuntimeError(f'truncated source: {path}')
    return cache[key]


def write_checked(root, entry, data):
    parts = safe_path(entry['path'])
    parent = root
    for part in parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise RuntimeError(f'symlink parent: {parent}')
        if not parent.exists():
            parent.mkdir()
        if not parent.is_dir():
            raise RuntimeError(f'non-directory parent: {parent}')
    target = parent / parts[-1]
    try:
        existing = target.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise RuntimeError(f'refusing non-regular destination: {target}')
    temporary = parent / ('.layers-' + hashlib.sha256(entry['path'].encode()).hexdigest()[:24])
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        temporary.chmod(0o755 if entry['mode'] == '100755' else 0o644)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='absent isolated directory')
    args = parser.parse_args()
    plan = read_plan(HERE / 'plan.json')
    dest = args.destination.absolute()
    ancestor = dest.parent
    while True:
        if ancestor.is_symlink() or not ancestor.is_dir():
            parser.error('destination parent chain must contain only real directories')
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent
    if dest.exists() or dest.is_symlink():
        parser.error('destination must be absent below an existing real directory')
    dest.mkdir(mode=0o700, exist_ok=False)
    run('git', 'init', '-q', str(dest))
    git(dest, 'remote', 'add', 'origin', REMOTE)
    pins = list(dict.fromkeys(plan['sources'].values()))
    git(dest, '-c', 'protocol.file.allow=never', 'fetch', '-q', '--no-tags',
        '--filter=blob:none', 'origin', *pins)
    for name, pin in plan['sources'].items():
        if git(dest, 'rev-parse', pin + '^{commit}').decode().strip() != pin:
            raise RuntimeError(f'missing public commit: {name} {pin}')
    git(dest, '-c', 'core.hooksPath=/dev/null', 'checkout', '-q', '--detach',
        plan['sources']['base'])
    # Fetch required blobs as one bounded pack rather than hundreds of implicit
    # per-blob promisor round trips. Every ID is independently pinned by a
    # commit-tree lookup in checked_blob before any bytes are used.
    required = list(dict.fromkeys(part['blob'] for entry in plan['files']
                                  for part in entry['parts'] if 'source' in part))
    missing = [oid for oid in required if subprocess.run(
        ['git', '-C', str(dest), 'cat-file', '-e', oid],
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
    if missing:
        git(dest, '-c', 'protocol.file.allow=never', 'fetch', '-q', '--no-tags',
            'origin', *missing)
    cache = {}
    for entry in plan['files']:
        path = entry['path']
        pieces = []
        for part in entry['parts']:
            if 'join' in part:
                pieces.append(part['join'].encode('utf-8'))
                continue
            data = checked_blob(dest, plan['sources'][part['source']], path,
                                part['blob'], cache)
            if 'lines' in part:
                start, end = part['lines']
                lines = data.splitlines(keepends=True)
                if end > len(lines):
                    raise RuntimeError(f'out-of-bounds source span: {path}')
                data = b''.join(lines[start - 1:end])
            pieces.append(data)
        composed = b''.join(pieces)
        if hashlib.sha256(composed).hexdigest() != entry['sha256']:
            raise RuntimeError(f'product digest mismatch: {path}')
        write_checked(dest, entry, composed)
    print(f"reconstructed {len(plan['files'])} product paths at {dest}")
    print(f"base={plan['sources']['base']} joins="
          f"{sum('join' in p for f in plan['files'] for p in f['parts'])}")


if __name__ == '__main__':
    main()
