"""Small shared helpers: atomic writes, YAML/JSON IO, hashing, slugs, APNs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            write(stream)
        Path(tmp).replace(path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path: Path, value: Any, indent: int | None = None) -> None:
    def write(stream):
        json.dump(value, stream, ensure_ascii=False, indent=indent,
                  separators=None if indent else (",", ":"), allow_nan=False)
        stream.write("\n")

    _atomic_write(Path(path), write)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    _atomic_write(Path(path), lambda stream: stream.write(text))


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def write_yaml(path: Path, value: Any) -> None:
    write_text(path, yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(text: str, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:60].strip("-") or fallback


def unique_id(base: str, existing: set[str]) -> str:
    candidate, n = base, 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def normalize_apn(value: Any, mode: str = "alnum") -> str:
    """Normalize an APN for joining. ``alnum`` drops punctuation and uppercases."""
    if value is None:
        return ""
    text = str(value).strip()
    if mode == "none":
        return text
    if mode == "digits":
        return re.sub(r"\D", "", text)
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


NUMBER_TEXT = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def to_number(value: Any) -> float | None:
    """Parse numbers stored as numbers or text; empty and non-numeric become None.

    Mirrored by ``toNumber`` in site/static/formula.js; keep them identical.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(",", "").replace("$", "")
    if not NUMBER_TEXT.match(text):
        return None
    return float(text)
