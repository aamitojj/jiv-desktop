#!/bin/sh
# Start mock LLM (11434) + Jiv (8765) with a fresh data dir. Extra env is passed through.
pkill -f "tests/mock_llm.py" ; pkill -f "run.py --port 8765"; sleep 1
D=${JIV_DATA_DIR:-$(mktemp -d /tmp/jivdata-XXXX)}; export JIV_DATA_DIR=$D
/workspace/venv/bin/python tests/mock_llm.py 11434 > /tmp/mock.log 2>&1 &
/workspace/venv/bin/python run.py --port 8765 --no-browser > /tmp/jiv.log 2>&1 &
for i in $(seq 40); do curl -s 127.0.0.1:8765/health >/dev/null && curl -s 127.0.0.1:11434/v1/models >/dev/null && break; sleep 0.5; done
echo "stack up, data=$D"
