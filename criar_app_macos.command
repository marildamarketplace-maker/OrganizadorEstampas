#!/bin/bash

set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$BASE_DIR/Organizador de Estampas.app"
SOURCE_FILE="$(mktemp -t organizador-estampas.XXXXXX.applescript)"

cleanup() {
  rm -f "$SOURCE_FILE"
}
trap cleanup EXIT

cat > "$SOURCE_FILE" <<'APPLESCRIPT'
on run
    set appPath to POSIX path of (path to me)
    set projectDir to do shell script "/usr/bin/dirname " & quoted form of appPath
    set startScript to projectDir & "/executar_macos.command"
    set logFile to (POSIX path of (path to library folder from user domain)) & "Logs/OrganizadorEstampas.log"

    try
        do shell script "/bin/test -f " & quoted form of startScript
    on error
        display alert "Organizador de Estampas" message "O arquivo executar_macos.command não foi encontrado ao lado do aplicativo." as critical
        return
    end try

    do shell script "/usr/bin/env ORGANIZADOR_SEM_TERMINAL=1 /usr/bin/nohup /bin/bash " & quoted form of startScript & " >> " & quoted form of logFile & " 2>&1 < /dev/null &"
end run
APPLESCRIPT

rm -rf "$APP_PATH"
/usr/bin/osacompile -o "$APP_PATH" "$SOURCE_FILE"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$APP_PATH/Contents/Info.plist"

echo
echo "Aplicativo criado:"
echo "$APP_PATH"
echo
echo "Nas próximas vezes, abra Organizador de Estampas.app."
