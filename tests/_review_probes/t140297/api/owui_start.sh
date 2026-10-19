#!/bin/bash
# Open WebUI 0.12.3 against the Hermes rig: sign-in off, bound to the Tailscale address only, data in /tmp.
set -u
HOST=${OWUI_HOST:-127.0.0.1}  # the address Open WebUI binds to (one interface only)
B=/tmp/h140297
RIG=$1
KEY=$(python3 -c "import json,sys; print(json.load(open('$RIG/rig.json'))['api_key'])")
mkdir -p $B/owui-data-$2 $B/hf
cd $B
export DATA_DIR=$B/owui-data-$2 HF_HOME=$B/hf WEBUI_AUTH=False ENABLE_OLLAMA_API=False \
  OPENAI_API_BASE_URL=http://127.0.0.1:18642/v1 OPENAI_API_KEY="$KEY" \
  BYPASS_EMBEDDING_AND_RETRIEVAL=true RAG_EMBEDDING_MODEL_AUTO_UPDATE=False OFFLINE_MODE=true \
  ENABLE_EVALUATION_ARENA_MODELS=False SCARF_NO_ANALYTICS=true DO_NOT_TRACK=true ANONYMIZED_TELEMETRY=false \
  WEBUI_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
nohup $B/owui-venv/bin/open-webui serve --host $HOST --port 8080 > $B/logs/owui-$2.log 2>&1 &
echo $! > $B/owui-$2.pid
for i in $(seq 1 90); do curl -s -o /dev/null -w '%{http_code}' http://$HOST:8080/health 2>/dev/null | grep -q 200 && { echo "owui up after ${i}x2s"; break; }; sleep 2; done
tail -5 $B/logs/owui-$2.log
