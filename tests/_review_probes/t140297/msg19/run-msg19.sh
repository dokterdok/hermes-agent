#!/bin/bash
# usage: run-msg19.sh <tree name> [tag]
set -u
export PATH=/opt/homebrew/bin:$PATH
B=/tmp/msg19
OLDPY=/tmp/h140297/devhome/installs/aaa10e0cf0d0216b/test-environment/gen-3f848b645f3c4858af9737941a1091cf/venv/bin/python
export HERMES_DEV_HOME=/tmp/h140297/devhome
cd $B/live
bash run.sh $B/$1 $B/runs check_msg19.py ${EXTRA:-} --old-root $B/pre106742 --old-python $OLDPY ${2:+--tag $2} > $B/logs/msg19-$1${2:+-$2}.log 2>&1
echo "msg19 $1 rc=$?"
grep -E "^[0-9:]+ (PASS|FAIL)" $B/logs/msg19-$1${2:+-$2}.log | cut -c1-220
