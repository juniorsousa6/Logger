#!/bin/bash
# Instala ou atualiza o Logger.app. Dois cliques para rodar.
# Compila o app no próprio Mac, então ele sempre sai certo para a versão do macOS
# e o processador (Apple Silicon ou Intel) desta máquina.
# Suas configurações (~/.cardlogger) e o histórico de cartões nunca são tocados.
set -euo pipefail
cd "$(dirname "$0")"
SRC="$PWD"
WAIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --update) ;;
    --wait-pid) WAIT="$2"; shift ;;
  esac
  shift
done
say() { echo "› $*"; }
fail() {
  echo "✕ $*"
  osascript -e "display dialog \"$*\" buttons {\"OK\"} default button 1 with title \"Logger\" with icon caution" >/dev/null 2>&1 || true
  exit 1
}

# 1. ferramentas da Apple (python3 + compilador swift)
if ! xcode-select -p >/dev/null 2>&1; then
  osascript -e 'display dialog "O Logger precisa das Ferramentas de Linha de Comando da Apple (grátis). Vou abrir o instalador da Apple; quando terminar, abra este instalador de novo." buttons {"OK"} default button 1 with title "Logger"' >/dev/null 2>&1 || true
  xcode-select --install >/dev/null 2>&1 || true
  exit 1
fi
command -v swiftc >/dev/null 2>&1 || fail "Compilador não encontrado. No Terminal, rode: xcode-select --install"
/usr/bin/python3 -c "import sys; assert sys.version_info >= (3, 8)" 2>/dev/null || fail "Python 3.8 ou mais novo é necessário. Rode: xcode-select --install"

# 2. se veio de dentro do app (atualização), espera ele fechar
if [ -n "$WAIT" ]; then
  for _ in $(seq 1 100); do kill -0 "$WAIT" 2>/dev/null || break; sleep 0.3; done
fi

VER="$(tr -d '[:space:]' < VERSION)"
OS="$(sw_vers -productVersion)"
UPD="$(tr -d '[:space:]' < mac/UPDATE_URL 2>/dev/null || true)"
KEY="$(tr -d '[:space:]' < mac/UPDATE_KEY 2>/dev/null || true)"
WORK="$(mktemp -d)"
APP="$WORK/Logger.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

say "Preparando o Logger $VER para macOS $OS ($(uname -m))…"
swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos11.0" -o "$APP/Contents/MacOS/Logger" mac/LoggerApp.swift \
  -framework Cocoa -framework WebKit -framework LocalAuthentication 2>"$WORK/build.log" || { cat "$WORK/build.log"; fail "Não consegui compilar o app. Me mande o texto que apareceu no Terminal."; }

cp app.py cardlogger.py media.py posrender.py fotos.py hds.py cards.json ui.html VERSION "$APP/Contents/Resources/app/"
sed -e "s|__VERSION__|$VER|g" -e "s|__UPDATE_URL__|$UPD|g" -e "s|__UPDATE_KEY__|$KEY|g" -e "s|__BUILT_ON__|$OS|g" \
  mac/Info.plist > "$APP/Contents/Info.plist"

# ícone
if [ -f mac/icon.png ]; then
  SET="$WORK/Logger.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s mac/icon.png --out "$SET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2)); sips -z $d $d mac/icon.png --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/Logger.icns" || true
fi

# assinatura do app
# Com um certificado "Developer ID Application" no Keychain deste Mac, o app sai assinado com ele
# (runtime endurecido + carimbo de hora da Apple). Assim a identidade do app é a mesma em toda versão:
# o macOS não pede de novo as permissões (cartões, HDs, Finder) a cada atualização.
# Sem certificado, assina só localmente, como antes. LOGGER_SIGN_ID força outra identidade.
SIGN_ID="${LOGGER_SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null | awk '/"Developer ID Application:/ {print $2; exit}')}"
SIGNED=""
if [ -n "$SIGN_ID" ]; then
  ENT=(); [ -f mac/Logger.entitlements ] && ENT=(--entitlements mac/Logger.entitlements)
  if codesign --force --options runtime --timestamp ${ENT[@]+"${ENT[@]}"} --sign "$SIGN_ID" "$APP" 2>"$WORK/sign.log"; then
    SIGNED="developer-id"
  elif codesign --force --options runtime --timestamp=none ${ENT[@]+"${ENT[@]}"} --sign "$SIGN_ID" "$APP" 2>>"$WORK/sign.log"; then
    SIGNED="developer-id-sem-carimbo"   # sem internet: vale neste Mac, mas não dá para notarizar
  else
    cat "$WORK/sign.log"
  fi
fi
if [ -n "$SIGNED" ] && codesign --verify --strict "$APP" 2>>"$WORK/sign.log"; then
  say "Assinado com $(codesign -dvv "$APP" 2>&1 | sed -n 's/^Authority=\(Developer ID Application: .*\)/\1/p' | head -1) ($SIGNED)"
else
  if [ -n "$SIGN_ID" ]; then say "Não consegui assinar com o Developer ID; vou assinar só neste Mac."; fi
  SIGNED=""
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true
fi

# 3. instala (troca a versão antiga só depois que a nova está pronta)
DEST="/Applications"; [ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"
if [ -d "$DEST/Logger.app" ]; then
  mv "$DEST/Logger.app" "$WORK/Logger-antigo.app"
fi
mv "$APP" "$DEST/Logger.app" || { [ -d "$WORK/Logger-antigo.app" ] && mv "$WORK/Logger-antigo.app" "$DEST/Logger.app"; fail "Não consegui instalar em $DEST."; }
xattr -dr com.apple.quarantine "$DEST/Logger.app" 2>/dev/null || true

# guarda o código para o app se refazer sozinho quando o macOS atualizar
SUP="$HOME/Library/Application Support/Logger/source"
mkdir -p "$SUP"
if [ "$SRC" != "$SUP" ]; then rsync -a --delete --exclude ".*" "$SRC/" "$SUP/"; fi

# remove o atalho antigo do terminal, se existir no mesmo lugar
say "Logger $VER instalado em $DEST/Logger.app"
open "$DEST/Logger.app"

# 4. notarização (opcional, em segundo plano: o app já abriu e você pode usar)
# Só roda se este Mac tiver o perfil do notarytool salvo no Keychain (nome em LOGGER_NOTARY_PROFILE,
# padrão "logger-notary"). A Apple confere o app e o "bilhete" fica grudado nele (stapler).
# Resultado em ~/Library/Logs/Logger-notarizacao.log. Sem perfil ou sem internet, nada muda.
PROFILE="${LOGGER_NOTARY_PROFILE:-logger-notary}"
if [ "$SIGNED" = "developer-id" ] && [ "${LOGGER_NO_NOTARIZE:-}" != "1" ] && xcrun --find notarytool >/dev/null 2>&1; then
  NLOG="$HOME/Library/Logs/Logger-notarizacao.log"
  mkdir -p "$(dirname "$NLOG")"
  say "Notarizando na Apple em segundo plano (registro: $NLOG)"
  nohup /bin/bash -c '
    APPX="$1"; PROF="$2"; V="$3"
    echo "[$(date "+%Y-%m-%d %H:%M:%S")] Logger $V: enviando para notarização"
    T="$(mktemp -d)"; trap "rm -rf \"$T\"" EXIT
    ditto -c -k --keepParent "$APPX" "$T/Logger.zip" || exit 1
    if xcrun notarytool submit "$T/Logger.zip" --keychain-profile "$PROF" --wait --timeout 30m; then
      xcrun stapler staple "$APPX" && echo "[$(date "+%Y-%m-%d %H:%M:%S")] Logger $V: notarizado e grampeado"
      spctl -a -vv "$APPX" 2>&1
    else
      echo "[$(date "+%Y-%m-%d %H:%M:%S")] Logger $V: notarização não concluída (perfil \"$PROF\" existe? internet?)"
    fi
  ' _ "$DEST/Logger.app" "$PROFILE" "$VER" >>"$NLOG" 2>&1 </dev/null &
  disown || true
fi
