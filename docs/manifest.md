# Схема манифеста

Один файл `skillpantry/catalog/<id>.toml` на запись. Проверка: `skillpantry validate`.

```toml
id = "context7"                 # = имя файла; латиница, цифры, дефис
number = 4                      # уникальный номер для выбора в меню
name = "Context7"               # отображаемое имя
category = "code-understanding" # ключ из catalog/_categories.toml
summary = "Одна строка: что и зачем"
description = """
Что делает, когда брать, что нужно для работы.
"""
status = "verified"             # verified | draft | reference | excluded
risk = "low"                    # low | medium | high
work_safe = true                # можно ли в рабочие (корпоративные) проекты
confirm = false                 # спрашивать подтверждение при установке (обязательно при high)
reviewed = "2026-11-02"         # дата последнего ревью (обязательна для verified)
tags = ["docs", "mcp"]
risk_notes = ["…"]              # что важно знать до установки
overlaps = ["other-id"]         # записи с похожим назначением

summary_en = "One line: what and why"    # английские тексты — обязательны в общем каталоге
description_en = """
What it does, when to use it, what it needs.
"""
risk_notes_en = ["…"]           # столько же, сколько в risk_notes
# [manual_en] — те же ключи и столько же шагов, что в [manual], если шаги написаны по-русски

[source]
repo = "upstash/context7"       # owner/repo на GitHub
ref = "18d6f5d6…"               # полный SHA коммита
license = "MIT"                 # SPDX; "none", если лицензии нет
url = "https://…"               # для справочных записей без репозитория
```

### Своя папка вместо репозитория

```toml
[source]
path = "D:/skills/go-review"    # полный путь к папке на этом компьютере
license = "own"
```

Только для своих записей (`~/.skillpantry/catalog/`, их пишет форма «Добавить своё» и
`skillpantry add <папка>`). Файлы ставятся прямо из папки; `ref` не пишется — версия считается как
хэш содержимого путей, которые ставит запись, и меняется, когда меняются файлы.

Свои записи нумеруются с 101, их `id` не может совпадать с записями общего каталога.

## Категории

`catalog/_categories.toml` — список направлений для первого шага мастера:

```toml
[design-frontend]
order = 2
title = "Дизайн и фронтенд"
description = "Что входит — показывается при фокусе на категории"
title_en = "Design and frontend"
description_en = "What is in it"
```

`skillpantry validate` проверяет, что категория каждой записи есть в этом файле. Новая категория —
новая таблица в файле; переименование — поменять ключ и `category` в манифестах.

Свои категории — в `~/.skillpantry/catalog/_categories.toml` (тот же формат; форма «Добавить своё»
пишет туда новую категорию). Они показываются вместе с общими.

## Что ставится

### Скиллы

```toml
[[skills]]
path = "skills/*"               # папка со SKILL.md или glob (каждая подпапка со SKILL.md — скилл)
name = "my-name"                # имя папки при установке (если путь один)
optional = true                 # ставится только с --optional или --skill <имя>
exclude = ["tests", "*.png"]    # что не копировать

[[skills]]                      # разные сборки для разных целей
paths = { claude-code = ".claude/skills/x", codex = ".agents/skills/x", default = "skills/x" }
```

`path = "."` — весь репозиторий и есть скилл (SKILL.md в корне).

| Цель | Проект | Глобально (`-g`) |
|---|---|---|
| claude-code | `.claude/skills/<имя>` | `~/.claude/skills/<имя>` |
| codex | `.agents/skills/<имя>` | `~/.agents/skills/<имя>` |
| gemini | `.gemini/skills/<имя>` | `~/.gemini/skills/<имя>` |
| cursor | `.cursor/skills/<имя>` | `~/.cursor/skills/<имя>` |
| folder | `<папка>/<id>/skills/<имя>` | — |

### Правила

```toml
[rules]
path = "CLAUDE.md"              # файл в репозитории с текстом правил
```

| Цель | Куда |
|---|---|
| claude-code | блок в `CLAUDE.md` (глобально `~/.claude/CLAUDE.md`) |
| codex | блок в `AGENTS.md` (глобально `~/.codex/AGENTS.md`) |
| gemini | блок в `GEMINI.md` (глобально `~/.gemini/GEMINI.md`) |
| cursor | файл `.cursor/rules/<id>.mdc` с `alwaysApply: true` (глобально — только вручную в настройках) |
| folder | `<папка>/<id>/RULES.md` |

Блок обрамляется маркерами `<!-- >>> skillpantry:<id> -->` … `<!-- <<< skillpantry:<id> -->`:
переустановка заменяет блок, удаление убирает только его.

### MCP-серверы

```toml
[mcp.context7]                  # имя сервера
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
env = { "API_KEY" = "", "MODE" = "fast" }      # "" или "${VAR}" — берётся из окружения

[mcp.remote]
url = "https://example.com/mcp"
headers = { "x-api-key" = "${REMOTE_KEY}" }
```

Значения секретов в манифест не пишутся никогда: только `""` или `"${VAR}"`. При установке они
превращаются в ссылку на переменную окружения в формате каждой цели.

| Цель | Файл | Формат |
|---|---|---|
| claude-code | `.mcp.json` | `mcpServers`, `${VAR}`; глобально — печатается команда `claude mcp add --scope user …` |
| codex | `.codex/config.toml` (глобально `~/.codex/config.toml`) | `[mcp_servers.x]`, `env_vars`, `env_http_headers` |
| gemini | `.gemini/settings.json` | `mcpServers`, `httpUrl` для удалённых |
| cursor | `.cursor/mcp.json` | `mcpServers`, `${env:VAR}` |
| folder | `<папка>/<id>/mcp.json` | как у Claude Code |

### Прочее

```toml
commands = [".claude/commands/*.md"]   # slash-команды, только Claude Code и folder

[variants]                              # выбрать один файл из многих
glob = "design-md/*/DESIGN.md"          # имя варианта = имя родительской папки
dest = "DESIGN.md"                      # куда положить в проекте

[workspace]                             # весь репозиторий как рабочая папка (только -t folder)
exclude = [".git", ".github"]

[targets]                               # переопределить поддержку целей
cursor = "none"                         # auto | manual | none

[manual]                                # шаги, которые skillpantry печатает, но не выполняет
any = ["npm install -g tool@1.2.3"]
claude-code = ["/plugin install x@y"]
```

Поддержка цели по умолчанию: есть что ставить → `auto`; нечего, но есть `manual` → `manual`;
иначе `none`. `workspace` — только `folder`.
