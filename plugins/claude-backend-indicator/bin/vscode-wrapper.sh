#!/usr/bin/env bash
# Wrapper para claudeCode.claudeProcessWrapper (extension Claude Code de VS Code).
#
# La extension nos invoca con la ruta del binario real como $1 y pasa el modelo
# de la pestana como `--model <id>`. Como --model SI es por pestana, sirve de
# selector de BACKEND:
#   qwen* / tooling / or-*  -> entorno de LiteLLM local
#   opus / sonnet / nada    -> sin tocar: Anthropic
#
# Se lee en CADA arranque de pestana, asi que los ajustes de abajo NO necesitan
# recargar la ventana: pestana nueva = configuracion nueva.
#   ~/.config/claude-local/env       entorno de LiteLLM (base url, key, ...)
#   ~/.config/claude-local/subagents modelo de los subagentes (una linea)
# Traza: ~/.cache/claude-vscode-wrapper.log
REAL="$1"; shift

LOG="$HOME/.cache/claude-vscode-wrapper.log"; mkdir -p "$(dirname "$LOG")"
CFG="$HOME/.config/claude-local"

want_local=0; prev=""; model=""; src="flag"
for a in "$@"; do
  if [ "$prev" = "--model" ]; then model="$a"; fi
  prev="$a"
done

# La extension de VS Code arranca el CLI SIN --model: el picker persiste la
# eleccion de la pestana en ~/.claude/settings.json y el CLI la lee de ahi.
# Como settings.json ya esta escrito cuando nos ejecutan, podemos consultarlo.
if [ -z "$model" ] && [ -r "$HOME/.claude/settings.json" ]; then
  model=$(python3 -c "
import json,os
try: print(json.load(open(os.path.expanduser('~/.claude/settings.json'))).get('model','') or '')
except Exception: print('')
" 2>/dev/null)
  src="settings"
fi

case "$model" in qwen*|tooling*|or-*) want_local=1 ;; esac

if [ "$want_local" = "1" ] && [ -r "$CFG/env" ]; then
  # shellcheck disable=SC1091
  . "$CFG/env"
  unset ANTHROPIC_MODEL          # el modelo lo manda --model de la pestana
  dest="LITELLM"
else
  dest="ANTHROPIC"
fi

# Modelo de los SUBAGENTES, independiente del de la pestana.
# Fichero con una linea: sonnet | opus | haiku | inherit  (o vacio/ausente = heredar)
sub=""
if [ -r "$CFG/subagents" ]; then
  sub="$(tr -d '[:space:]' < "$CFG/subagents")"
fi
case "$sub" in
  ""|inherit) : ;;                                  # sin default -> heredan del padre
  *) export CLAUDE_CODE_SUBAGENT_MODEL="$sub" ;;
esac

printf '%s model=%s (%s) -> %s subagents=%s\n' \
  "$(date -Is)" "${model:-<none>}" "$src" "$dest" "${sub:-<hereda>}" >> "$LOG"
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"

exec "$REAL" "$@"
