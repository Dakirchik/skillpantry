# Changelog

## 0.5.0 — 2026-11-02

First public release. Everything below (0.1.0–0.4.1) was built before publishing and ships in it.

- **New name: skillpantry** (was skillshelf — that name is taken by several unrelated projects).
  Commands are `skillpantry`, `pantry` and `pantryctl`; data lives in `~/.skillpantry`. Running the
  installer over an old install moves `~/.skillshelf`, replaces the old commands and PATH entry, and
  blocks written under the old name in CLAUDE.md / AGENTS.md / config.toml are still updated and removed.
- **English interface.** The full-screen UI, the text menu, all commands, `pantryctl.ps1` and
  `pantryctl.sh`, and every catalog text (summaries, descriptions, risks, manual steps, categories)
  are in English and Russian. The language follows the OS; change it on the home screen
  (Language / Язык), with `skillpantry lang en|ru|auto`, `--lang`, `SKILLPANTRY_LANG`, or
  `pantryctl install --lang` / `-Lang`. Switching takes effect immediately, no restart.
- Search finds entries by their texts in either language.
- Shims are marked "created by pantryctl"; uninstall also recognizes the older Russian marker.

## 0.4.1 — 2026-11-02

- Windows: Python was "not found" under Windows PowerShell 5.1 (install.cmd) even when installed —
  5.1 does not escape `"` in arguments of native programs, so the version check sent python broken
  code. The check now uses single quotes only; CI runs the script in 5.1-style argument mode.
- Windows: Python is also looked up in the registry (PEP 514) and in Program Files, like agent-recall.
- Yes/no prompts read `[Y/n]` / `[y/N]` (Enter = the capital option); y, yes, д, да are accepted.
- Footers list every navigation key: → details, ←/Backspace/Esc back; they shorten step by step on
  narrow terminals instead of cutting keys off. On the home screen → opens the item.

## 0.4.0 — 2026-11-02

- Your own entries: "Add your own" form in the full-screen UI (and the text menu). A folder or file on
  this computer, or a GitHub repo → detected skills, rules files, `.mcp.json` and commands with an
  audit → name, id, category (existing or a new one with title and description), tags (existing or
  new), summary, description, what to install, risk, work-safe, "reviewed by me" for GitHub.
  Save, or save and open the install wizard with it.
- Own entries live in `~/.skillpantry/catalog/` (`SKILLPANTRY_USER_CATALOG`, `user_catalog` in
  config.toml) together with own categories; numbered from 101, marked ★, editable and removable
  from the catalog browser. Updating skillpantry never touches them; `pantryctl uninstall --purge`
  asks separately and keeps them with `-y`.
- Local sources: `[source] path = "…"`; files are installed straight from the folder, the version is
  a content hash of the installed paths, so changed files show up as "↑" and can be updated.
- CLI: `add <folder|file>` and `add <url> --mine` (`--name`, `--summary`, `--category`,
  `--category-title`, `--tags`, `--risk`, `--reviewed`), `drop <id>`.
- Draft entries are shown as unavailable in the wizard with the reason, instead of being skipped
  at the end.

## 0.3.0 — 2026-11-02

- Full-screen interface (no dependencies): checkboxes, radio lists, search, a details panel that
  follows the cursor, scrollable views. Windows via msvcrt + ANSI (Windows 10+), macOS/Linux via termios.
- Install wizard: categories → entries → target → location → options → plan; reinstalling an entry
  updates it and says so in the plan.
- Catalog browser and an "installed" screen to update or remove selected installs.
- Nine categories with descriptions in `catalog/_categories.toml`; `validate` checks every entry's category.
- Install options (variant, optional skills, skill filters) are remembered and reused on update.
- `--plain` / `SKILLPANTRY_PLAIN=1` keeps the text menu; it is also used automatically when the
  terminal cannot show the full-screen UI.

## 0.2.0 — 2026-11-02

- `pantryctl`: native install and management on every OS, as in agent-recall.
  - Windows: `pantryctl.ps1` + double-click `install.cmd` (Windows PowerShell 5.1 and PowerShell 7);
    Python and Git via winget if missing; commands in `%LOCALAPPDATA%\skillpantry\bin` on the user PATH.
  - macOS / Linux: `pantryctl.sh` + `install.sh` (and `install.command` for Finder); Python and Git via
    Homebrew, apt, dnf, pacman, zypper or apk; commands in `~/.local/bin`, PATH line in the shell profile
    between markers, removed exactly on uninstall.
  - `install | update | uninstall [--purge] | status | paths | config | doctor`.
- `.cmd` shims survive non-ASCII user names (paths via `%LOCALAPPDATA%` / `%USERPROFILE%`, otherwise
  the OEM code page cmd.exe reads).
- `~/.skillpantry/config.toml` for `library` and `catalog`; environment variables still win.
- Line endings and UTF-8 BOM of edited files (CLAUDE.md, AGENTS.md, JSON/TOML configs, manifests) are
  preserved on every OS.
- CI: tests on Windows, macOS and Ubuntu with Python 3.11 and 3.13; real install → use → uninstall
  through both scripts.

## 0.1.0 — 2026-11-02

- Catalog of 44 reviewed entries, installer for Claude Code, Codex, Gemini CLI, Cursor and a plain
  folder, static audit, pinned commits, full uninstall, catalog upkeep (`add`, `bump`, `approve`),
  Obsidian export.
