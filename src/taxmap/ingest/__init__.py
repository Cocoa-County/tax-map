"""Add resources (files, web snapshots, chats, notes) to a project's registry."""

from __future__ import annotations

import mimetypes
import re
import shutil
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from taxmap.domain.models import Resource, ResourceKind
from taxmap.project import RESOURCE_DIRS, Project, ProjectError
from taxmap.util import now_iso, sha256_file, slugify, unique_id, write_text

USER_AGENT = "taxmap/0.1 (+https://github.com/Cocoa-County/tax-map; public-records research)"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/markdown", ".md")


def guess_media_type(path: Path) -> str:
    with Path(path).open("rb") as stream:
        head = stream.read(8)
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def infer_kind(media_type: str, from_web: bool = False) -> ResourceKind:
    if media_type == "application/pdf" or "word" in media_type or "officedocument" in media_type:
        return "document"
    if media_type.startswith("image/"):
        return "image"
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "web" if from_web else "document"
    if media_type in {"text/csv", "application/json", "application/geo+json"} or "spreadsheet" in media_type:
        return "data"
    if media_type in {"text/markdown", "text/plain"}:
        return "note"
    return "other"


def _register(project: Project, path: Path, *, kind: ResourceKind | None, title: str | None,
              resource_id: str | None, origin_url: str | None = None, retrieved_at: str | None = None,
              tags: list[str] | None = None, notes: str | None = None, created: bool = True) -> Resource:
    media_type = guess_media_type(path)
    digest = sha256_file(path)
    existing = project.resources()
    for resource in existing:
        if resource.sha256 == digest and resource.path:
            # Same bytes already registered: drop our new copy and reuse the existing record.
            if created and project.path(resource.path) != path:
                path.unlink()
            return resource
    rid = resource_id or unique_id(slugify(title or path.stem, "resource"), {r.id for r in existing})
    if any(r.id == rid for r in existing):
        raise ProjectError(f"Resource id already exists: {rid}")
    resource = Resource(
        id=rid, kind=kind or infer_kind(media_type, origin_url is not None), path=project.rel(path),
        title=title or path.stem, origin_url=origin_url, media_type=media_type, sha256=digest,
        size=path.stat().st_size, retrieved_at=retrieved_at, added_at=now_iso(), tags=tags or [], notes=notes,
    )
    return project.upsert_resource(resource)


def _target(project: Project, kind: ResourceKind, filename: str) -> Path:
    directory = project.root / RESOURCE_DIRS[kind]
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", filename).strip("._") or "file"
    stem, suffix = Path(safe).stem[:80], Path(safe).suffix[:10]
    candidate, n = directory / f"{stem}{suffix}", 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def add_file(project: Project, source: str | Path, *, kind: ResourceKind | None = None,
             title: str | None = None, resource_id: str | None = None, origin_url: str | None = None,
             tags: list[str] | None = None, notes: str | None = None) -> Resource:
    """Copy a local file into the project (or register it in place if already inside)."""
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"Not a file: {source}")
    created = project.root not in source.parents
    if not created:
        path = source
    else:
        media_type = guess_media_type(source)
        path = _target(project, kind or infer_kind(media_type), source.name)
        shutil.copy2(source, path)
    return _register(project, path, kind=kind, title=title or source.stem, resource_id=resource_id,
                     origin_url=origin_url, tags=tags, notes=notes, created=created)


def _filename_from_response(url: str, headers: Message, media_type: str) -> str:
    disposition = headers.get("Content-Disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I)
    name = unquote(match.group(1)) if match else unquote(Path(urlparse(url).path).name)
    if not Path(name).suffix:
        name = (name or "page") + (mimetypes.guess_extension(media_type) or ".bin")
    return name


def add_url(project: Project, url: str, *, title: str | None = None, kind: ResourceKind | None = None,
            resource_id: str | None = None, tags: list[str] | None = None, notes: str | None = None,
            link_only: bool = False, timeout: float = 60) -> Resource:
    """Snapshot a public URL into the project, preserving its origin and retrieval time."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ProjectError("Only http(s) URLs are supported")
    if link_only:
        existing = project.resources()
        rid = resource_id or unique_id(slugify(title or parsed.path or parsed.netloc), {r.id for r in existing})
        resource = Resource(id=rid, kind=kind or "web", title=title or url, origin_url=url,
                            added_at=now_iso(), tags=tags or [], notes=notes)
        return project.upsert_resource(resource)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        media_type = (response.headers.get_content_type() or "application/octet-stream").lower()
        body = response.read(MAX_DOWNLOAD_BYTES + 1)
        final_url = response.geturl()
        headers = response.headers
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ProjectError(f"Download exceeds {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB: {url}")
    if body.startswith(b"%PDF"):
        media_type = "application/pdf"
    resolved_kind = kind or infer_kind(media_type, from_web=True)
    path = _target(project, resolved_kind, _filename_from_response(final_url, headers, media_type))
    path.write_bytes(body)
    if title is None and media_type == "text/html":
        match = re.search(rb"<title[^>]*>(.*?)</title>", body[:200_000], re.I | re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1).decode("utf-8", "replace")).strip()[:200]
    return _register(project, path, kind=resolved_kind, title=title or path.stem, resource_id=resource_id,
                     origin_url=url, retrieved_at=now_iso(), tags=tags, notes=notes)


def add_chat(project: Project, title: str, messages: list[dict] | str, *, tags: list[str] | None = None,
             notes: str | None = None) -> Resource:
    """Store a conversation transcript (list of {role, content} or Markdown text)."""
    if isinstance(messages, str):
        text = messages
    else:
        parts = [f"# {title}\n"]
        for message in messages:
            role = str(message.get("role", "unknown")).strip() or "unknown"
            when = f" ({message['time']})" if message.get("time") else ""
            parts.append(f"## {role}{when}\n\n{str(message.get('content', '')).strip()}\n")
        text = "\n".join(parts)
    path = _target(project, "chat", f"{slugify(title, 'chat')}.md")
    write_text(path, text if text.endswith("\n") else text + "\n")
    return _register(project, path, kind="chat", title=title, resource_id=None, tags=tags, notes=notes)


def add_note(project: Project, title: str, text: str, *, tags: list[str] | None = None) -> Resource:
    path = _target(project, "note", f"{slugify(title, 'note')}.md")
    write_text(path, text if text.endswith("\n") else text + "\n")
    return _register(project, path, kind="note", title=title, resource_id=None, tags=tags)


def annotate(project: Project, resource_id: str, *, title: str | None = None, tags: list[str] | None = None,
             notes: str | None = None, origin_url: str | None = None) -> Resource:
    resource = project.resource(resource_id)
    update = {k: v for k, v in {"title": title, "tags": tags, "notes": notes, "origin_url": origin_url}.items()
              if v is not None}
    return project.upsert_resource(resource.model_copy(update=update))
