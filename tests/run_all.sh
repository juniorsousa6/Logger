#!/bin/bash
# Roda todos os testes do Logger: motor, servidor e interface.
#   bash tests/run_all.sh          (precisa de python3; a parte da interface precisa de node + playwright)
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
FAIL=0
echo "== motor e regras =="
"$PY" -m unittest tests.test_engine || FAIL=1
echo "== servidor (endpoints) =="
"$PY" -m unittest tests.test_api || FAIL=1
echo "== revisao 1.3.3 (achados do relatorio) =="
"$PY" -m unittest tests.test_revisao || FAIL=1

if command -v node >/dev/null && node -e "require('playwright')" 2>/dev/null; then
  echo "== interface (todos os botoes) =="
  S="$(mktemp -d)"
  "$PY" tests/e2e_setup.py "$S" >/dev/null
  PORT=$((20000 + RANDOM % 20000))
  CARDLOGGER_HOME="$S/home" CARDLOGGER_VOLUMES="$S/V" LOGGER_PORT=$PORT LOGGER_NO_BROWSER=1 \
    PATH="$S/bin:$PATH" "$PY" app.py >"$S/server.log" 2>&1 &
  SRV=$!
  sleep 1.5
  S="$S" U="http://127.0.0.1:$PORT/" SHOTS="${SHOTS:-$S}" node tests/e2e_ui.js || FAIL=1
  kill $SRV 2>/dev/null
  echo "(telas e log do servidor em $S)"
else
  echo "== interface: pulada (instale node e playwright para rodar) =="
fi
[ $FAIL = 0 ] && echo "TUDO PASSOU" || echo "HOUVE FALHAS"
exit $FAIL
