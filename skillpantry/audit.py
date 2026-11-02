"""Static review helper: flag patterns in fetched content a human should look at.

This is not a security guarantee. It points the reviewer at the lines worth
reading before an entry is approved (see CONTRIBUTING.md).
"""

from __future__ import annotations

from skillpantry.i18n import T

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

TEXT_EXT = {".md", ".mdc", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh", ".bash",
            ".ps1", ".cmd", ".bat", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ""}
SCRIPT_EXT = {".sh", ".bash", ".ps1", ".cmd", ".bat", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
MAX_BYTES = 2_000_000

# (severity, code, description, regex)
PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    ("high", "pipe-to-shell", "download and run code in one command",
     re.compile(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b|irm[^\n|]*\|\s*iex|iwr[^\n|]*\|\s*iex", re.I)),
    ("high", "hidden-instruction", "attempt to override instructions or hide actions from the user",
     re.compile(r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(system|previous)"
                r"|do not (tell|inform|show) the user|without (telling|informing|asking) the user"
                r"|не (сообщай|говори) пользователю", re.I)),
    ("high", "secret-read", "reading keys and credentials",
     re.compile(r"\.ssh/|id_rsa|\.aws/credentials|\.netrc|keychain|Login Data|\.npmrc|\.pypirc", re.I)),
    ("high", "destructive", "dangerous delete commands",
     re.compile(r"rm\s+-rf\s+(/|~|\$HOME)(\s|$)|Remove-Item\s+[^\n]*-Recurse[^\n]*(C:\\|\$HOME|~)", re.I)),
    ("medium", "obfuscation", "encoded or dynamically executed code",
     re.compile(r"base64\s+(-d|--decode)|FromBase64String|\beval\s*\(|new Function\(|exec\(\s*compile", re.I)),
    ("medium", "auto-install", "installing or running remote packages",
     re.compile(r"\b(npx\s+-y|npm\s+(i|install)\s+-g|pip3?\s+install|pipx\s+install|uvx\s|uv\s+tool\s+install"
                r"|cargo\s+install|brew\s+install|winget\s+install)\b", re.I)),
    ("medium", "env-write", "changes to agent configs, the shell profile or environment variables",
     re.compile(r"\.bashrc|\.zshrc|\$PROFILE|settings\.json|\.claude\.json|setx\s", re.I)),
    ("low", "network", "network requests from scripts",
     re.compile(r"\b(fetch\(|requests\.(get|post)|urllib\.request|http\.client|axios|Invoke-WebRequest)", re.I)),
]

URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
BENIGN_HOSTS = {"github.com", "raw.githubusercontent.com", "example.com", "localhost",
                "www.w3.org", "json.schemastore.org", "shields.io", "img.shields.io"}


@dataclass
class Finding:
    severity: str
    code: str
    description: str
    file: str
    line: int
    text: str


@dataclass
class AuditReport:
    root: Path
    files: int = 0
    scripts: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    hosts: Counter = field(default_factory=Counter)
    findings: list[Finding] = field(default_factory=list)

    def counts(self) -> Counter:
        return Counter(f.severity for f in self.findings)

    @property
    def worst(self) -> str:
        c = self.counts()
        return "high" if c["high"] else "medium" if c["medium"] else "low" if c["low"] else "none"


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.name != ".skillpantry-ok":
            yield p


def audit_paths(paths: list[Path], root: Path) -> AuditReport:
    rep = AuditReport(root=root)
    seen: set[Path] = set()
    for base in paths:
        for f in iter_files(base):
            if f in seen:
                continue
            seen.add(f)
            rep.files += 1
            rel = f.relative_to(root).as_posix() if f.is_relative_to(root) else f.as_posix()
            if f.suffix.lower() in SCRIPT_EXT or (f.suffix == "" and _is_shebang(f)):
                rep.scripts.append(rel)
            if "hooks" in f.parts and f.suffix == ".json":
                rep.hooks.append(rel)
            if f.suffix.lower() not in TEXT_EXT or f.stat().st_size > MAX_BYTES:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for host in URL_RE.findall(text):
                if host.lower() not in BENIGN_HOSTS:
                    rep.hosts[host.lower()] += 1
            for i, line in enumerate(text.splitlines(), 1):
                for sev, code, desc, rx in PATTERNS:
                    if rx.search(line):
                        rep.findings.append(Finding(sev, code, desc, rel, i, line.strip()[:200]))
    return rep


def _is_shebang(f: Path) -> bool:
    try:
        with f.open("rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def format_report(rep: AuditReport, limit: int = 12) -> str:
    c = rep.counts()
    lines = [
        T(f"Files: {rep.files}  ·  scripts: {len(rep.scripts)}  ·  hooks: {len(rep.hooks)}  ·  " f"findings: high {c['high']}, medium {c['medium']}, low {c['low']}", f"Файлов: {rep.files}  ·  скриптов: {len(rep.scripts)}  ·  хуков: {len(rep.hooks)}  ·  "
        f"находок: высоких {c['high']}, средних {c['medium']}, низких {c['low']}"),
    ]
    if rep.hosts:
        top = ", ".join(f"{h} ({n})" for h, n in rep.hosts.most_common(6))
        lines.append(T(f"External hosts: {top}", f"Внешние адреса: {top}"))
    if rep.hooks:
        lines.append(T("Hooks: ", "Хуки: ") + ", ".join(rep.hooks[:6]))
    order = {"high": 0, "medium": 1, "low": 2}
    shown = 0
    by_code: Counter = Counter()
    for f in sorted(rep.findings, key=lambda x: (order[x.severity], x.code, x.file, x.line)):
        by_code[f.code] += 1
        if by_code[f.code] > 3 or shown >= limit:
            continue
        shown += 1
        lines.append(f"  [{f.severity}] {f.code}: {f.file}:{f.line}  {f.text}")
    hidden = len(rep.findings) - shown
    if hidden > 0:
        lines.append(T(f"  … {hidden} more (full list: skillpantry audit <id> --all)", f"  … ещё {hidden} (полный список: skillpantry audit <id> --all)"))
    return "\n".join(lines)
