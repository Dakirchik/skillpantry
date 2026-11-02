"""Text files that keep their line endings and BOM on every OS.

Path.write_text() turns "\n" into "\r\n" on Windows, which would rewrite a whole
CLAUDE.md or config file and show up as noise in git diffs; Windows editors also
like to add a UTF-8 BOM. These helpers read with universal newlines, remember the
file's style, and write it back the same way ("\n", no BOM for new files).
"""

from __future__ import annotations

from pathlib import Path

BOM = b"\xef\xbb\xbf"
Style = tuple[str, bool]  # (line ending, has BOM)
DEFAULT: Style = ("\n", False)


def read(path: Path) -> tuple[str, Style]:
    """Return (text with '\\n' newlines, original style). Missing file -> ('', DEFAULT)."""
    if not path.exists():
        return "", DEFAULT
    raw = path.read_bytes()
    bom = raw.startswith(BOM)
    text = raw[len(BOM):].decode("utf-8", errors="replace") if bom else raw.decode("utf-8", errors="replace")
    eol = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), (eol, bom)


def write(path: Path, text: str, style: Style = DEFAULT) -> None:
    eol, bom = style
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\r\n", "\n")
    if eol != "\n":
        data = data.replace("\n", eol)
    path.write_bytes((BOM if bom else b"") + data.encode("utf-8"))
