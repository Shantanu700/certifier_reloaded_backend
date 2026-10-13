import random
import re
import string

DEFAULT_FILENAME = "certificate_{index}"
MAX_COMPONENT_LENGTH = 50
UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")
RANDOM_STRING_CHARSET = string.ascii_lowercase + string.digits


def _sanitize(value: str) -> str:
    "Collapse anything not filesystem/URL-safe to '_' and cap length -- CSV values and fixed literals are untrusted-ish input."
    cleaned = UNSAFE_CHARS_RE.sub("_", value.strip())
    return cleaned[:MAX_COMPONENT_LENGTH]


def _render_part(part: dict, index: int, row: dict) -> str:
    if part["type"] == "csv_key":
        return _sanitize(row.get(part["key"], ""))
    if part["type"] == "sequence":
        return str(part.get("start", 1) + index * part.get("step", 1))
    if part["type"] == "random":
        charset = string.digits if part.get("kind") == "number" else RANDOM_STRING_CHARSET
        return "".join(random.choices(charset, k=part.get("length", 6)))
    return _sanitize(part["value"])  # fixed


def resolve_filename(batch, index: int, row: dict) -> str:
    "Base filename (no extension) for one row, per batch.naming_convention, or the current default if unset."
    if not batch.naming_convention:
        return DEFAULT_FILENAME.format(index=index)
    name = "".join(_render_part(part, index, row) for part in batch.naming_convention)
    return name or DEFAULT_FILENAME.format(index=index)
