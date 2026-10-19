#!/bin/bash
# MSG-19 live check trees. Never touches ~/.hermes.
set -u
export PATH=/opt/homebrew/bin:$PATH
B=/tmp/msg19
export HERMES_HOME=/tmp/h140297/devhome HERMES_RUNTIME_DIR=/tmp/h140297/devhome/tools
for pair in main:ddabd3837862022a6053331fe3ba97cf9716b9e3 prefix:f17021fbec01d7f6b1277d897c97b639b9a15765 pre106742:d04056d609bb53530843a6c630493243b2ebdc24; do
  name=${pair%%:*}; sha=${pair#*:}
  if [ ! -d $B/$name/.git ]; then
    git init -q $B/$name && git -C $B/$name fetch -q --depth 1 https://github.com/NousResearch/hermes-agent.git $sha && git -C $B/$name checkout -q --detach FETCH_HEAD
  fi
  echo "$name $(git -C $B/$name rev-parse --short=10 HEAD)"
done
for name in main prefix pre106742; do
  ( cd $B/$name && LEFTHOOK=0 scripts/run-in-hermes-env python -c "import sys; print(sys.version.split()[0], sys.executable)" ) > $B/logs/provision-$name.log 2>&1
  echo "$name provision rc=$? $(tail -1 $B/logs/provision-$name.log)"
done
