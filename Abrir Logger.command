#!/bin/bash
# Duplo clique para abrir o Logger.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display dialog "O Logger precisa do Python 3. O macOS vai oferecer instalar as Ferramentas de Linha de Comando; aceite e abra o Logger de novo." buttons {"OK"}'
  xcode-select --install
  exit 1
fi
exec python3 app.py
