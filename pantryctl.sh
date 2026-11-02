#!/usr/bin/env bash
# pantryctl - install and manage skillpantry on Linux and macOS.
#
# skillpantry's logic is Python; this script handles the system side: finds Python 3.11+ and
# Git (suggests how to install them), creates the skillpantry / pantry / pantryctl commands in
# ~/.local/bin, adds PATH to the shell profile, writes config.toml, updates and removes the
# installation. User projects are never touched.
#
#   ./pantryctl.sh install [--library DIR] [--catalog DIR] [--python PATH] [--lang en|ru|auto] [-y] [--no-path]
#   pantryctl status | update | uninstall [--purge] | paths | config | doctor | help
#
# Language: --lang, SKILLPANTRY_LANG, `language` in config.toml, then LANG / the macOS language.
# Compatible with bash 3.2 (the macOS default).

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
APP_DIR="${SKILLPANTRY_APPDIR:-${XDG_DATA_HOME:-$HOME/.local/share}/skillpantry}"
BIN_DIR="${SKILLPANTRY_BINDIR:-$HOME/.local/bin}"
DATA_DIR="${SKILLPANTRY_HOME:-$HOME/.skillpantry}"
STATE_FILE="$APP_DIR/install.env"
CONFIG_FILE="$DATA_DIR/config.toml"
MARK_BEGIN="# >>> skillpantry >>>"
MARK_END="# <<< skillpantry <<<"
MIN_MAJOR=3
MIN_MINOR=11

YES=0; PURGE=0; NO_PATH=0; LIBRARY=""; CATALOG=""; PYTHON_OPT=""; LANG_OPT=""

# ---- language: same priority as in Python (skillpantry/i18n.py) ----------------------------
detect_lang() {
  local v
  case "$LANG_OPT" in en|ru) echo "$LANG_OPT"; return ;; esac
  v="$(printf '%s' "${SKILLPANTRY_LANG:-}" | cut -c1-2 | tr '[:upper:]' '[:lower:]')"
  case "$v" in en|ru) echo "$v"; return ;; esac
  if [ "$LANG_OPT" != auto ] && [ -f "$CONFIG_FILE" ]; then
    v="$({ grep -E '^[[:space:]]*language[[:space:]]*=[[:space:]]*"(en|ru)' "$CONFIG_FILE" || true; } | head -n1 | sed -E 's/.*"(en|ru).*/\1/')"
    case "$v" in en|ru) echo "$v"; return ;; esac
  fi
  for v in "${LC_ALL:-}" "${LC_MESSAGES:-}" "${LANG:-}"; do
    case "$v" in ""|C|C.*|POSIX) continue ;; ru*) echo ru; return ;; *) echo en; return ;; esac
  done
  if [ "$(uname -s)" = Darwin ] && defaults read -g AppleLanguages 2>/dev/null | grep -q '"\{0,1\}ru'; then
    echo ru; return
  fi
  echo en
}
UI_LANG="$(detect_lang)"
t() { if [ "$UI_LANG" = ru ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }  # t "English" "Русский"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_OFF=""
fi
step() { printf '\n%s> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '  %s[ok]%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
info() { printf '  %s\n' "$1"; }
warn() { printf '  %s[!]%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
fail() { printf '  %s[x]%s %s\n' "$C_RED" "$C_OFF" "$1" >&2; exit 1; }

ask() {  # ask "question" [default:y|n]
  local default="${2:-y}" hint answer
  if [ "$YES" = 1 ]; then return 0; fi
  if [ "$default" = y ]; then hint="Y/n"; else hint="y/N"; fi  # Enter = the capital option
  printf '  %s (%s): ' "$1" "$hint"
  if ! read -r answer; then answer=""; fi
  [ -z "$answer" ] && answer="$default"
  case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
    y|yes|д|да|Д|Да|ДА|l) return 0 ;;
    *) return 1 ;;
  esac
}

version() {
  sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "$SOURCE/skillpantry/__init__.py" 2>/dev/null || echo "?"
}

os_name() {
  case "$(uname -s)" in
    Darwin) echo macos ;;
    Linux) echo linux ;;
    *) echo other ;;
  esac
}

# ---- Python ------------------------------------------------------------------------

py_check() {  # py_check EXE -> prints "path version" for Python 3.11+, else fails
  "$1" -c "import sys
if sys.version_info < ($MIN_MAJOR, $MIN_MINOR): raise SystemExit(1)
print(sys.executable, '%d.%d.%d' % sys.version_info[:3])" 2>/dev/null
}

find_python() {
  local c out
  if [ -n "$PYTHON_OPT" ]; then
    py_check "$PYTHON_OPT"  # an explicit --python is used as is or not at all
    return
  fi
  for c in python3.14 python3.13 python3.12 python3.11 python3 python \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if out="$(py_check "$c")"; then printf '%s\n' "$out"; return 0; fi
    fi
  done
  return 1
}

pkg_hint() {  # pkg_hint python|git -> prints an install command for this system
  local what="$1"
  case "$(os_name)" in
    macos)
      if command -v brew >/dev/null 2>&1; then
        if [ "$what" = python ]; then echo "brew install python@3.12"; else echo "brew install git"; fi
      elif [ "$what" = git ]; then echo "xcode-select --install"
      else echo ""; fi ;;
    linux)
      local pkg="python3"; [ "$what" = git ] && pkg="git"
      if command -v apt-get >/dev/null 2>&1; then echo "sudo apt-get install -y $pkg"
      elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install -y $pkg"
      elif command -v pacman >/dev/null 2>&1; then echo "sudo pacman -S --needed $([ "$what" = git ] && echo git || echo python)"
      elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install -y $pkg"
      elif command -v apk >/dev/null 2>&1; then echo "sudo apk add $pkg"
      else echo ""; fi ;;
    *) echo "" ;;
  esac
}

ensure_python() {
  step "Python $MIN_MAJOR.$MIN_MINOR+"
  local found cmd
  if ! found="$(find_python)"; then
    [ -z "$PYTHON_OPT" ] || fail "$(t "$PYTHON_OPT is not Python $MIN_MAJOR.$MIN_MINOR+ (check the path in --python)" "$PYTHON_OPT — не Python $MIN_MAJOR.$MIN_MINOR+ (проверьте путь в --python)")"
    warn "$(t "Python $MIN_MAJOR.$MIN_MINOR+ not found" "Python $MIN_MAJOR.$MIN_MINOR+ не найден")"
    cmd="$(pkg_hint python)"
    if [ -n "$cmd" ] && ask "$(t "Run: $cmd ?" "Выполнить: $cmd ?")"; then
      sh -c "$cmd" || warn "$(t "the command failed" "команда завершилась с ошибкой")"
      hash -r
      found="$(find_python)" || true
    fi
    if [ -z "${found:-}" ]; then
      fail "$(t "Install Python 3.11+ (https://www.python.org/downloads/) and run again. If the system one is old, point at another: --python /path/to/python3.12" "Установите Python 3.11+ (https://www.python.org/downloads/) и повторите. Если версия в системе старая — укажите другую: --python /путь/к/python3.12")"
    fi
  fi
  PY_PATH="${found% *}"
  PY_VERSION="${found##* }"
  ok "Python $PY_VERSION: $PY_PATH"
}

ensure_git() {
  step "Git"
  local cmd
  if ! command -v git >/dev/null 2>&1; then
    warn "$(t "Git not found — it is needed to fetch skills at pinned commits" "Git не найден — он нужен, чтобы скачивать скиллы с закреплённых коммитов")"
    cmd="$(pkg_hint git)"
    if [ -n "$cmd" ] && ask "$(t "Run: $cmd ?" "Выполнить: $cmd ?")"; then sh -c "$cmd" || warn "$(t "the command failed" "команда завершилась с ошибкой")"; hash -r; fi
  fi
  if command -v git >/dev/null 2>&1; then ok "$(git --version)"
  else warn "$(t "Git not found: without it skillpantry cannot fetch sources (https://git-scm.com)" "Git не найден: без него skillpantry не сможет скачивать источники (https://git-scm.com)")"; fi
}

# ---- commands on PATH --------------------------------------------------------------------

sh_quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

write_shims() {
  step "$(t "Commands skillpantry, pantry, pantryctl" "Команды skillpantry, pantry, pantryctl")"
  mkdir -p "$BIN_DIR"
  local n f
  for n in skillpantry pantry; do
    f="$BIN_DIR/$n"
    {
      echo '#!/bin/sh'
      echo "# created by pantryctl: $SOURCE"
      echo "PYTHONPATH=$(sh_quote "$SOURCE")\${PYTHONPATH:+:\$PYTHONPATH}"
      echo 'export PYTHONPATH'
      echo "exec $(sh_quote "$PY_PATH") -X utf8 -m skillpantry \"\$@\""
    } > "$f"
    chmod +x "$f"
  done
  f="$BIN_DIR/pantryctl"
  {
    echo '#!/bin/sh'
    echo "# created by pantryctl: $SOURCE"
    echo "exec bash $(sh_quote "$SOURCE/pantryctl.sh") \"\$@\""
  } > "$f"
  chmod +x "$f"
  ok "$(t "in $BIN_DIR" "в $BIN_DIR")"
}

in_path() {
  case ":$PATH:" in *":$1:"*|*":$1/:"*) return 0 ;; *) return 1 ;; esac
}

rc_file() {  # the user's shell profile that gets the PATH line
  local sh; sh="$(basename "${SHELL:-sh}")"
  case "$sh" in
    zsh) echo "${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash) if [ "$(os_name)" = macos ]; then echo "$HOME/.bash_profile"; else echo "$HOME/.bashrc"; fi ;;
    fish) echo "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/skillpantry.fish" ;;
    *) echo "$HOME/.profile" ;;
  esac
}

add_to_path() {
  if [ "$NO_PATH" = 1 ]; then info "$(t "PATH left as is (--no-path). The commands are in $BIN_DIR" "PATH не меняю (--no-path). Команды лежат в $BIN_DIR")"; return; fi
  local rc line
  rc="$(rc_file)"
  if [ -f "$rc" ] && grep -qF "$MARK_BEGIN" "$rc"; then ok "$(t "PATH is already set in $rc" "PATH уже настроен в $rc")"; return; fi
  if in_path "$BIN_DIR"; then ok "$(t "$BIN_DIR is already on PATH" "$BIN_DIR уже в PATH")"; return; fi
  if ! ask "$(t "Add $BIN_DIR to PATH (a line in $rc)?" "Добавить $BIN_DIR в PATH (строка в $rc)?")"; then
    warn "$(t "Add it yourself: export PATH=\"$BIN_DIR:\$PATH\"" "Добавьте сами: export PATH=\"$BIN_DIR:\$PATH\"")"; return
  fi
  case "$rc" in
    *.fish) line="fish_add_path $(sh_quote "$BIN_DIR")" ;;
    *) line="export PATH=$(sh_quote "$BIN_DIR"):\"\$PATH\"" ;;
  esac
  mkdir -p "$(dirname "$rc")"
  printf '\n%s\n%s\n%s\n' "$MARK_BEGIN" "$line" "$MARK_END" >> "$rc"
  ok "$(t "added to $rc (new terminal windows will see the commands; in this one: source $rc)" "добавлено в $rc (новые окна терминала увидят команды; в этом: source $rc)")"
}

remove_from_path() {
  local rc tmp
  for rc in "$HOME/.zshrc" "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" \
            "${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d/skillpantry.fish"; do
    [ -f "$rc" ] || continue
    grep -qF "$MARK_BEGIN" "$rc" || continue
    tmp="$(mktemp)"
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
      { line[NR] = $0 }
      END {
        out = 0; skip = 0
        for (i = 1; i <= NR; i++) {
          if (line[i] == b) { if (out > 0 && buf[out] == "") out--; skip = 1; continue }
          if (line[i] == e) { skip = 0; continue }
          if (!skip) buf[++out] = line[i]
        }
        for (i = 1; i <= out; i++) print buf[i]
      }' "$rc" > "$tmp"
    cat "$tmp" > "$rc" && rm -f "$tmp"
    case "$rc" in *skillpantry.fish) [ -s "$rc" ] || rm -f "$rc" ;; esac
    ok "$(t "PATH removed from $rc" "убран PATH из $rc")"
  done
}

# ---- config.toml ----------------------------------------------------------------------------

toml_str() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

abs_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    "~"*) printf '%s\n' "$HOME${1#\~}" ;;
    *) printf '%s/%s\n' "$(pwd -P)" "$1" ;;
  esac
}

set_config() {  # set_config KEY VALUE
  local key="$1" line tmp
  line="$key = $(toml_str "$2")"
  tmp="$(mktemp)"
  if grep -qE "^[[:space:]]*#?[[:space:]]*${key}[[:space:]]*=" "$CONFIG_FILE"; then
    awk -v k="$key" -v l="$line" '
      !done && $0 ~ "^[[:space:]]*#?[[:space:]]*" k "[[:space:]]*=" { print l; done = 1; next }
      { print }' "$CONFIG_FILE" > "$tmp"
  else
    cat "$CONFIG_FILE" > "$tmp"; printf '%s\n' "$line" >> "$tmp"
  fi
  cat "$tmp" > "$CONFIG_FILE" && rm -f "$tmp"
}

ensure_config() {
  step "$(t "Settings" "Настройки")"
  mkdir -p "$DATA_DIR"
  if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" <<EOF
# skillpantry settings. SKILLPANTRY_* environment variables win over this file.

# Interface language: "en" or "ru" (empty - follow the system)
# language = "en"

# Folder for the folder target (-t folder) when -d is not given
# library = $(toml_str "$HOME/skillpantry-library")

# Your own folder with catalog manifests (default - skillpantry/catalog in the sources)
# catalog = "/path/to/my-catalog"
EOF
    ok "$(t "created $CONFIG_FILE" "создан $CONFIG_FILE")"
  else ok "$(t "exists: $CONFIG_FILE" "есть $CONFIG_FILE")"; fi
  if [ -n "$LANG_OPT" ]; then
    if [ "$LANG_OPT" = auto ]; then set_config language ""; else set_config language "$LANG_OPT"; fi
    ok "language = $LANG_OPT"
  fi
  if [ -n "$LIBRARY" ]; then set_config library "$(abs_path "$LIBRARY")"; ok "library = $LIBRARY"; fi
  if [ -n "$CATALOG" ]; then set_config catalog "$(abs_path "$CATALOG")"; ok "catalog = $CATALOG"; fi
}

# ---- state and running the Python part --------------------------------------------------------------

save_state() {
  mkdir -p "$APP_DIR"
  {
    echo "VERSION=$(sh_quote "$(version)")"
    echo "SRC=$(sh_quote "$SOURCE")"
    echo "PY=$(sh_quote "$PY_PATH")"
    echo "BIN=$(sh_quote "$BIN_DIR")"
    echo "INSTALLED_AT=$(sh_quote "$(date '+%Y-%m-%d %H:%M')")"
  } > "$STATE_FILE"
}

load_state() {
  [ -f "$STATE_FILE" ] || return 1
  VERSION=""; SRC=""; PY=""; BIN=""; INSTALLED_AT=""
  # shellcheck disable=SC1090
  . "$STATE_FILE"
}

pantry() {  # run the Python part with the installed interpreter
  load_state || fail "$(t "skillpantry is not installed. Run: ./pantryctl.sh install" "skillpantry не установлен. Запустите: ./pantryctl.sh install")"
  PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" "$PY" -X utf8 -m skillpantry "$@"
}

# ---- commands -------------------------------------------------------------------------

# ---- before 0.5.0 the project was called skillshelf: take over that installation --------------
migrate_legacy() {
  local f rc tmp legacy_app="${XDG_DATA_HOME:-$HOME/.local/share}/skillshelf" moved=0
  # data (install journal, own entries, config): Python moves it and fixes paths in the journal
  if [ -z "${SKILLPANTRY_HOME:-}" ] && [ -d "$HOME/.skillshelf" ] && [ ! -e "$DATA_DIR" ]; then
    PYTHONPATH="$SOURCE" "$PY_PATH" -c 'from skillpantry.config import home_dir; home_dir()' \
      && [ -d "$DATA_DIR" ] && ok "$(t "data moved: ~/.skillshelf → $DATA_DIR" "данные перенесены: ~/.skillshelf → $DATA_DIR")"
  fi
  for f in skillshelf shelf shelfctl; do
    if [ -f "$BIN_DIR/$f" ] && grep -qE "created by shelfctl|создано shelfctl" "$BIN_DIR/$f"; then
      rm -f "$BIN_DIR/$f"; moved=1
    fi
  done
  # the PATH line stays, only its markers get the new name
  for rc in "$HOME/.zshrc" "${ZDOTDIR:-$HOME}/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$rc" ] || continue
    grep -qF "# >>> skillshelf >>>" "$rc" || continue
    tmp="$(mktemp)"
    sed -e 's/^# >>> skillshelf >>>$/# >>> skillpantry >>>/' -e 's/^# <<< skillshelf <<<$/# <<< skillpantry <<</' "$rc" > "$tmp"
    cat "$tmp" > "$rc" && rm -f "$tmp"; moved=1
  done
  rc="${XDG_CONFIG_HOME:-$HOME/.config}/fish/conf.d"
  if [ -f "$rc/skillshelf.fish" ]; then
    sed -e 's/skillshelf >>>/skillpantry >>>/' -e 's/skillshelf <<</skillpantry <<</' "$rc/skillshelf.fish" > "$rc/skillpantry.fish"
    rm -f "$rc/skillshelf.fish"; moved=1
  fi
  if [ -d "$legacy_app" ]; then rm -f "$legacy_app/install.env"; rmdir "$legacy_app" 2>/dev/null || true; moved=1; fi
  if [ "$moved" = 1 ]; then ok "$(t "the old skillshelf commands were replaced" "старые команды skillshelf заменены")"; fi
}

cmd_install() {
  printf '%sskillpantry %s — %s%s\n' "$C_BOLD" "$(version)" "$(t install установка)" "$C_OFF"
  info "$(t "sources: $SOURCE" "исходники: $SOURCE")"
  [ -f "$SOURCE/skillpantry/__init__.py" ] || fail "$(t "No skillpantry folder next to the script — run pantryctl.sh from the repository root" "Рядом со скриптом нет папки skillpantry — запустите pantryctl.sh из корня репозитория")"
  ensure_python
  ensure_git
  migrate_legacy
  write_shims
  add_to_path
  ensure_config
  save_state
  step "$(t Check Проверка)"
  pantry validate || warn "$(t "the catalog has errors (see above) — the install works, but those entries should be fixed" "в каталоге есть ошибки (см. выше) — установка работает, но эти записи лучше поправить")"
  printf '\n%s%s%s\n' "$C_GREEN" "$(t Done. Готово.)" "$C_OFF"
  info "$(t "Open a new terminal window and run:  skillpantry" "Откройте новое окно терминала и запустите:  skillpantry")"
  info "$(t "List for Claude Code:                skillpantry list -t claude-code" "Список для Claude Code:                       skillpantry list -t claude-code")"
  info "$(t "Manage the installation:             pantryctl status | update | uninstall" "Управление установкой:                        pantryctl status | update | uninstall")"
  info "$(t "Language:                            skillpantry lang en | ru" "Язык:                                         skillpantry lang en | ru")"
  info "$(t "The sources stay here and are used directly: $SOURCE" "Исходники остаются здесь и используются напрямую: $SOURCE")"
  info "$(t "If you move the folder — run install again." "Если переместите папку — запустите install ещё раз.")"
}

cmd_update() {
  load_state || fail "$(t "skillpantry is not installed. Run: ./pantryctl.sh install" "skillpantry не установлен. Запустите: ./pantryctl.sh install")"
  step "$(t "Updating the sources" "Обновление исходников")"
  if [ -d "$SOURCE/.git" ] && command -v git >/dev/null 2>&1; then
    if git -C "$SOURCE" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      git -C "$SOURCE" pull --ff-only || warn "$(t "git pull failed (local changes?) — continuing with the current sources" "git pull не прошёл (локальные изменения?) — продолжаю с текущими исходниками")"
    else
      info "$(t "the branch has no upstream (git push -u origin main) — skipping git pull" "у ветки нет удалённого репозитория (git push -u origin main) — пропускаю git pull")"
    fi
  else
    info "$(t "not a git repository — update the files yourself (a new archive) and run update again" "не git-репозиторий — обновите файлы сами (новый архив) и повторите update")"
  fi
  local found
  if found="$(py_check "$PY")"; then
    PY_PATH="${found% *}"; PY_VERSION="${found##* }"
  else
    warn "$(t "the saved Python no longer works, looking again" "сохранённый Python больше не работает, ищу заново")"; ensure_python
  fi
  BIN_DIR="$BIN"
  write_shims
  save_state
  step "$(t Check Проверка)"
  pantry validate || true
  info "$(t "Entries with a newer commit in the catalog: skillpantry status --all" "Записи, у которых в каталоге новее коммит, покажет: skillpantry status --all")"
}

cmd_uninstall() {
  step "$(t "Removing skillpantry" "Удаление skillpantry")"
  local installs="$DATA_DIR/installs.json" n
  if load_state; then BIN_DIR="$BIN"; fi
  if [ "$PURGE" = 1 ] && [ -f "$installs" ]; then
    # grep exits 1 when nothing matches; with pipefail that would stop the script
    n="$({ grep -o '"installed_at"' "$installs" || true; } | wc -l | tr -d ' ')"
    if [ "${n:-0}" -gt 0 ]; then
      warn "$(t "skillpantry installed entries in $n place(s). Project files stay, but skillpantry uninstall will no longer be able to remove them." "skillpantry поставил записи в $n мест(а). Файлы в проектах останутся, но удалить их командой uninstall потом будет нельзя.")"
      info "$(t "See: skillpantry status --all   ·   remove: skillpantry uninstall <id> -t <target> ..." "Посмотреть: skillpantry status --all   ·   удалить: skillpantry uninstall <id> -t <цель> ...")"
      ask "$(t "Delete the install journal and cache anyway?" "Всё равно удалить журнал установок и кэш?")" n || PURGE=0
    fi
  fi
  local f
  for f in skillpantry pantry pantryctl; do
    # remove only our own files (older versions wrote the marker in Russian)
    if [ -f "$BIN_DIR/$f" ] && grep -qE "created by pantryctl|создано pantryctl" "$BIN_DIR/$f"; then rm -f "$BIN_DIR/$f"; fi
  done
  remove_from_path
  rm -f "$STATE_FILE"
  rmdir "$APP_DIR" 2>/dev/null || true
  ok "$(t "commands and PATH removed" "команды и PATH убраны")"
  if [ "$PURGE" = 1 ]; then
    if [ -d "$DATA_DIR" ]; then
      local own=0 keep_own=0
      if [ -d "$DATA_DIR/catalog" ]; then
        own="$({ ls "$DATA_DIR/catalog" 2>/dev/null || true; } | { grep -v '^_' || true; } | { grep -c '\.toml$' || true; })"
      fi
      if [ "${own:-0}" -gt 0 ]; then
        warn "$(t "$DATA_DIR/catalog holds your own entries ($own) — there are no other copies." "В $DATA_DIR/catalog ваши собственные записи ($own) — копий больше нигде нет.")"
        # with -y your own entries are never deleted: only on an explicit answer
        if [ "$YES" = 1 ]; then keep_own=1
        elif ask "$(t "Delete your own entries too?" "Удалить и свои записи?")" n; then keep_own=0
        else keep_own=1; fi
      fi
      if [ "$keep_own" = 1 ]; then
        find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name catalog -exec rm -rf {} +
        ok "$(t "data removed: $DATA_DIR (your own entries kept in $DATA_DIR/catalog)" "удалены данные: $DATA_DIR (свои записи оставлены в $DATA_DIR/catalog)")"
      else
        rm -rf "$DATA_DIR"; ok "$(t "data removed: $DATA_DIR" "удалены данные: $DATA_DIR")"
      fi
    fi
  else
    info "$(t "Data (install journal, cache, backups, config.toml, your own entries) kept: $DATA_DIR  (--purge removes it)" "Данные (журнал установок, кэш, резервные копии, config.toml, свои записи) оставлены: $DATA_DIR  (--purge удалит)")"
  fi
  info "$(t "Sources left untouched: $SOURCE" "Исходники не трогаю: $SOURCE")"
}

cmd_status() {
  printf '%sskillpantry %s%s\n' "$C_BOLD" "$(version)" "$C_OFF"
  if ! load_state; then warn "$(t "not installed (./pantryctl.sh install)" "не установлен (./pantryctl.sh install)")"; return 0; fi
  local pyinfo note
  if pyinfo="$(py_check "$PY")"; then pyinfo="  (${pyinfo##* })"; else pyinfo="$(t "  [!] does not start — pantryctl update" "  [!] не запускается — pantryctl update")"; fi
  note=""; [ -d "$SRC" ] || note="$(t "  [!] the folder is gone — run install from the new folder" "  [!] папки нет — запустите install из новой папки")"
  info "$(t "installed:    $INSTALLED_AT  (version $VERSION)" "установлен:   $INSTALLED_AT  (версия $VERSION)")"
  info "$(t "sources:      " "исходники:    ")$SRC$note"
  info "python:       $PY$pyinfo"
  if in_path "$BIN"; then info "$(t "commands:     " "команды:      ")$BIN"; else info "$(t "commands:     " "команды:      ")$BIN$(t "  (not on PATH in this window — open a new one)" "  (в этом окне не в PATH — откройте новое)")"; fi
  info "$(t "data:         " "данные:       ")$DATA_DIR"
  if [ -f "$CONFIG_FILE" ]; then info "$(t "config:       " "конфиг:       ")$CONFIG_FILE"; else info "$(t "config:       " "конфиг:       ")$CONFIG_FILE$(t "  (none)" "  (нет)")"; fi
  info "$(t "language:     " "язык:         ")$UI_LANG"
  echo
  pantry status --all
}

cmd_paths() {
  info "$(t "sources     " "исходники   ")$SOURCE"
  info "$(t "program     " "программа   ")$APP_DIR"
  info "$(t "commands    " "команды     ")$BIN_DIR"
  info "$(t "data        " "данные      ")$DATA_DIR"
  info "$(t "config      " "конфиг      ")$CONFIG_FILE"
  info "$(t "own entries " "свои записи ")$DATA_DIR/catalog"
  info "$(t "cache       " "кэш         ")$DATA_DIR/cache"
  info "$(t "journal     " "журнал      ")$DATA_DIR/installs.json"
  info "$(t "backups     " "копии       ")$DATA_DIR/backups"
}

cmd_config() {
  [ -f "$CONFIG_FILE" ] || ensure_config
  if [ -n "${EDITOR:-}" ]; then "$EDITOR" "$CONFIG_FILE"
  elif [ "$(os_name)" = macos ]; then open -t "$CONFIG_FILE"
  else info "$CONFIG_FILE"; fi
}

cmd_help() {
  if [ "$UI_LANG" = ru ]; then
    cat <<'EOF'
pantryctl — установка и управление skillpantry (Linux, macOS)

  install [--library DIR] [--catalog DIR] [--python PATH] [--lang en|ru|auto] [-y] [--no-path]
                    Python 3.11+ и Git (подскажет команду установки) -> команды
                    skillpantry, pantry, pantryctl в ~/.local/bin -> PATH в профиле
                    оболочки -> config.toml -> проверка каталога
  update            git pull исходников, пересоздать команды, проверить каталог
  uninstall [--purge]
                    убрать команды и строку PATH; с --purge ещё журнал, кэш и
                    config.toml (файлы в проектах не трогаются; свои записи — только по ответу)
  status            что установлено, где, какие записи стоят в проектах
  paths             все пути
  config            открыть config.toml ($EDITOR)
  doctor            проверка окружения (skillpantry doctor)

Работа с каталогом — команда skillpantry (или pantry):  skillpantry --help
Язык: --lang en|ru, SKILLPANTRY_LANG или skillpantry lang en|ru
EOF
    return
  fi
  cat <<'EOF'
pantryctl — install and manage skillpantry (Linux, macOS)

  install [--library DIR] [--catalog DIR] [--python PATH] [--lang en|ru|auto] [-y] [--no-path]
                    Python 3.11+ and Git (suggests the install command) -> skillpantry,
                    pantry, pantryctl commands in ~/.local/bin -> PATH in the shell
                    profile -> config.toml -> catalog check
  update            git pull the sources, recreate the commands, check the catalog
  uninstall [--purge]
                    remove the commands and the PATH line; with --purge also the journal,
                    cache and config.toml (project files stay; your own entries only if you say so)
  status            what is installed and where, which entries are in projects
  paths             all paths
  config            open config.toml ($EDITOR)
  doctor            environment check (skillpantry doctor)

Working with the catalog — the skillpantry (or pantry) command:  skillpantry --help
Language: --lang en|ru, SKILLPANTRY_LANG or skillpantry lang en|ru
EOF
}

# ---- arguments -------------------------------------------------------------

COMMAND="${1:-help}"
[ $# -gt 0 ] && shift
while [ $# -gt 0 ]; do
  case "$1" in
    -y|--yes) YES=1 ;;
    --purge) PURGE=1 ;;
    --no-path) NO_PATH=1 ;;
    --library) LIBRARY="${2:?--library needs a path}"; shift ;;
    --catalog) CATALOG="${2:?--catalog needs a path}"; shift ;;
    --python) PYTHON_OPT="${2:?--python needs a path}"; shift ;;
    --lang)
      LANG_OPT="${2:?--lang needs en, ru or auto}"; shift
      case "$LANG_OPT" in en|ru|auto) ;; *) printf '%s--lang: en, ru, auto%s\n' "$C_RED" "$C_OFF" >&2; exit 2 ;; esac
      UI_LANG="$(detect_lang)" ;;
    *) printf '%s%s %s%s\n' "$C_RED" "$(t "Unknown option:" "Неизвестный параметр:")" "$1" "$C_OFF" >&2; cmd_help; exit 2 ;;
  esac
  shift
done

case "$COMMAND" in
  install) cmd_install ;;
  update) cmd_update ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  paths) cmd_paths ;;
  config) cmd_config ;;
  doctor) pantry doctor ;;
  help|-h|--help) cmd_help ;;
  *) printf '%s%s %s%s\n' "$C_RED" "$(t "Unknown command:" "Неизвестная команда:")" "$COMMAND" "$C_OFF" >&2; cmd_help; exit 2 ;;
esac
