"""Where each AI tool looks for skills, rules and MCP servers, and in what format."""

from __future__ import annotations

from skillpantry.i18n import T

import json
import re
from dataclasses import dataclass
from pathlib import Path

from skillpantry.catalog import McpServer

VAR_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


@dataclass
class Layout:
    target: str          # claude-code | codex | gemini | cursor | folder
    scope: str           # project | global | folder
    root: Path           # project root, home directory, or destination folder

    @property
    def label(self) -> str:
        where = {"project": T("project", "проект"), "global": T("global", "глобально"), "folder": T("folder", "папка")}[self.scope]
        return f"{self.target} · {where} · {self.root}"

    # ---- skills ---------------------------------------------------------
    def skills_dir(self, entry_id: str) -> Path | None:
        r, g = self.root, self.scope == "global"
        return {
            "claude-code": r / ".claude" / "skills",
            "codex": r / ".agents" / "skills",
            "gemini": r / ".gemini" / "skills",
            "cursor": r / ".cursor" / "skills",
            "folder": r / entry_id / "skills",
        }[self.target] if not (g and self.target == "folder") else None

    # ---- rules ----------------------------------------------------------
    def rules_file(self, entry_id: str) -> tuple[Path, str] | None:
        """(path, mode) where mode is 'block' (managed block) or 'file' (own file)."""
        r, g = self.root, self.scope == "global"
        if self.target == "claude-code":
            return (r / ".claude" / "CLAUDE.md" if g else r / "CLAUDE.md"), "block"
        if self.target == "codex":
            return (r / ".codex" / "AGENTS.md" if g else r / "AGENTS.md"), "block"
        if self.target == "gemini":
            return (r / ".gemini" / "GEMINI.md" if g else r / "GEMINI.md"), "block"
        if self.target == "cursor":
            # Cursor user-level rules live in the app settings, not on disk.
            return None if g else (r / ".cursor" / "rules" / f"{entry_id}.mdc", "file")
        return r / entry_id / "RULES.md", "file"

    # ---- MCP --------------------------------------------------------------
    def mcp_file(self, entry_id: str) -> tuple[Path, str] | None:
        r, g = self.root, self.scope == "global"
        if self.target == "claude-code":
            # User-scope MCP lives in ~/.claude.json together with app state;
            # we do not edit it and print a `claude mcp add` command instead.
            return None if g else (r / ".mcp.json", "claude")
        if self.target == "codex":
            return r / ".codex" / "config.toml", "codex"
        if self.target == "gemini":
            return r / ".gemini" / "settings.json", "gemini"
        if self.target == "cursor":
            return r / ".cursor" / "mcp.json", "cursor"
        return r / entry_id / "mcp.json", "claude"

    # ---- slash commands -----------------------------------------------------
    def commands_dir(self, entry_id: str) -> Path | None:
        if self.target == "claude-code":
            return self.root / ".claude" / "commands"
        if self.target == "folder":
            return self.root / entry_id / "commands"
        return None

    def variant_root(self, entry_id: str) -> Path | None:
        if self.scope == "global":
            return None
        return self.root / entry_id if self.target == "folder" else self.root


def env_name(key: str, value: str) -> str:
    """Manifest env/header value -> environment variable name ('' or ${VAR})."""
    m = VAR_RE.match(value or "")
    if m:
        return m.group(1)
    return re.sub(r"[^A-Z0-9_]", "_", key.upper())


def is_var(value: str) -> bool:
    return value == "" or bool(VAR_RE.match(value))


def mcp_json(server: McpServer, fmt: str) -> dict:
    """Server config for JSON-based clients. Secrets stay as variable references."""
    def ref(key: str, value: str) -> str:
        if not is_var(value):
            return value
        name = env_name(key, value)
        return {"cursor": f"${{env:{name}}}"}.get(fmt, f"${{{name}}}")

    if server.url:
        headers = {k: ref(k, v) for k, v in server.headers.items()}
        if fmt == "gemini":
            out = {"httpUrl": server.url}
        elif fmt == "cursor":
            out = {"url": server.url}
        else:
            out = {"type": "http", "url": server.url}
        if headers:
            out["headers"] = headers
        return out
    out: dict = {"command": server.command, "args": list(server.args)}
    if server.env:
        out["env"] = {k: ref(k, v) for k, v in server.env.items()}
    return out


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def mcp_codex_toml(server: McpServer) -> str:
    lines = [f"[mcp_servers.{_toml_key(server.name)}]"]
    if server.url:
        lines.append(f"url = {_toml_str(server.url)}")
        plain = {k: v for k, v in server.headers.items() if not is_var(v)}
        envh = {k: env_name(k, v) for k, v in server.headers.items() if is_var(v)}
        if plain:
            lines.append("http_headers = { " + ", ".join(
                f"{_toml_str(k)} = {_toml_str(v)}" for k, v in plain.items()) + " }")
        if envh:
            lines.append("env_http_headers = { " + ", ".join(
                f"{_toml_str(k)} = {_toml_str(v)}" for k, v in envh.items()) + " }")
    else:
        lines.append(f"command = {_toml_str(server.command)}")
        lines.append("args = [" + ", ".join(_toml_str(a) for a in server.args) + "]")
        plain = {k: v for k, v in server.env.items() if not is_var(v)}
        passthrough = [env_name(k, v) for k, v in server.env.items() if is_var(v)]
        if plain:
            lines.append("env = { " + ", ".join(
                f"{_toml_str(k)} = {_toml_str(v)}" for k, v in plain.items()) + " }")
        if passthrough:
            lines.append("env_vars = [" + ", ".join(_toml_str(v) for v in passthrough) + "]")
    return "\n".join(lines) + "\n"


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_str(name)


def claude_mcp_add_command(server: McpServer) -> str:
    """Command the user can run for a user-scope MCP server in Claude Code."""
    if server.url:
        parts = ["claude mcp add --scope user --transport http", server.name, server.url]
        for k, v in server.headers.items():
            val = f"${{{env_name(k, v)}}}" if is_var(v) else v
            parts += ["--header", f'"{k}: {val}"']
        return " ".join(parts)
    parts = ["claude mcp add --scope user", server.name]
    for k, v in server.env.items():
        val = f"${{{env_name(k, v)}}}" if is_var(v) else v
        parts += ["-e", f"{k}={val}"]
    parts += ["--", server.command, *server.args]
    return " ".join(parts)


def required_env(server: McpServer) -> list[str]:
    return [env_name(k, v) for k, v in {**server.env, **server.headers}.items() if is_var(v)]
