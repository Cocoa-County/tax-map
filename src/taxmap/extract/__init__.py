"""Deterministic content extraction: page text, tables, positioned words, page images.

Outputs are cached under ``derived/<resource-id>/`` keyed by the resource sha256, so
agents can re-read pages cheaply and cite 1-based PDF page numbers.
"""

from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser
from pathlib import Path

from taxmap.domain.models import Resource
from taxmap.project import Project, ProjectError
from taxmap.util import now_iso, read_json, write_json, write_text

TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json", "application/geo+json"}


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = None

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            if self._href and data.strip():
                self.links.append((data.strip(), self._href))


def html_to_text(html: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _HTMLText()
    parser.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip(), parser.links


def _require_file(project: Project, resource: Resource) -> Path:
    if not resource.path:
        raise ProjectError(f"Resource {resource.id} is link-only; snapshot it with resource_add_url first")
    return project.path(resource.path, must_exist=True)


def _meta_path(project: Project, rid: str) -> Path:
    return project.derived_dir(rid) / "meta.json"


def extract_text(project: Project, resource_id: str, force: bool = False) -> dict:
    """Extract per-page text. Returns a summary including pages that look image-only."""
    resource = project.resource(resource_id)
    source = _require_file(project, resource)
    meta_path = _meta_path(project, resource_id)
    if meta_path.exists() and not force:
        meta = read_json(meta_path)
        if meta.get("sha256") == resource.sha256:
            return meta
    out = project.derived_dir(resource_id) / "pages"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):
        old.unlink()
    media = resource.media_type or ""
    pages: list[str]
    links: list[tuple[str, str]] = []
    if media == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(source))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception as error:  # damaged pages should not abort the document
                pages.append(f"[text extraction failed: {error}]")
        method = "pypdf"
    elif media in {"text/html", "application/xhtml+xml"}:
        text, links = html_to_text(source.read_text(encoding="utf-8", errors="replace"))
        pages, method = [text], "html"
    elif media in TEXT_TYPES or media.startswith("text/"):
        pages, method = [source.read_text(encoding="utf-8", errors="replace")], "text"
    elif media.startswith("image/"):
        pages, method = [""], "image (no text layer; use render/vision or OCR)"
    else:
        raise ProjectError(f"No text extractor for media type {media!r}")
    for number, text in enumerate(pages, 1):
        write_text(out / f"{number:04d}.txt", text)
    empty = [n for n, text in enumerate(pages, 1) if len(text.strip()) < 20]
    meta = {
        "resource": resource_id, "sha256": resource.sha256, "method": method, "pages": len(pages),
        "characters": sum(len(t) for t in pages), "image_only_pages": empty,
        "extracted_at": now_iso(),
    }
    if links:
        meta["links"] = [{"text": t, "href": h} for t, h in links[:500]]
    if empty and media == "application/pdf":
        meta["warning"] = ("Some pages have no text layer (scanned/image-only). Use render_page_image "
                           "and read them visually; do not treat missing text as a negative finding.")
    write_json(meta_path, meta, indent=2)
    if resource.pages != len(pages):
        project.upsert_resource(resource.model_copy(update={"pages": len(pages)}))
    return meta


def read_pages(project: Project, resource_id: str, start: int = 1, end: int | None = None,
               max_chars: int = 60_000) -> dict:
    meta = extract_text(project, resource_id)
    end = min(end or start, meta["pages"])
    if start < 1 or start > meta["pages"]:
        raise ProjectError(f"Page {start} out of range 1..{meta['pages']}")
    directory = project.derived_dir(resource_id) / "pages"
    result, used, truncated = [], 0, False
    for number in range(start, end + 1):
        text = (directory / f"{number:04d}.txt").read_text(encoding="utf-8")
        if used + len(text) > max_chars:
            text = text[: max(0, max_chars - used)]
            truncated = True
        result.append({"page": number, "text": text})
        used += len(text)
        if truncated:
            break
    return {"resource": resource_id, "total_pages": meta["pages"], "pages": result, "truncated": truncated}


def _pdf(project: Project, resource_id: str):
    resource = project.resource(resource_id)
    if resource.media_type != "application/pdf":
        raise ProjectError(f"{resource_id} is not a PDF")
    import pdfplumber

    return pdfplumber.open(str(_require_file(project, resource)))


def extract_tables(project: Project, resource_id: str, page: int, strategy: str = "lines") -> dict:
    """Extract tables from one PDF page with pdfplumber; also saved as CSV under derived/."""
    settings = {"lines": {}, "text": {"vertical_strategy": "text", "horizontal_strategy": "text"}}[strategy]
    with _pdf(project, resource_id) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ProjectError(f"Page {page} out of range 1..{len(pdf.pages)}")
        tables = pdf.pages[page - 1].extract_tables(settings) or []
    directory = project.derived_dir(resource_id) / "tables"
    saved = []
    for index, table in enumerate(tables, 1):
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows([[c or "" for c in row] for row in table])
        path = directory / f"p{page:04d}_t{index}.csv"
        write_text(path, buffer.getvalue())
        saved.append(project.rel(path))
    return {"resource": resource_id, "page": page, "strategy": strategy, "tables": tables, "saved": saved}


def extract_words(project: Project, resource_id: str, page: int,
                  bbox: tuple[float, float, float, float] | None = None, limit: int = 4000) -> dict:
    """Positioned words (x0, top, x1, bottom in PDF points) for column-position parsing."""
    with _pdf(project, resource_id) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise ProjectError(f"Page {page} out of range 1..{len(pdf.pages)}")
        target = pdf.pages[page - 1]
        if bbox:
            target = target.crop(bbox)
        words = target.extract_words(keep_blank_chars=False, use_text_flow=False)
        size = {"width": float(pdf.pages[page - 1].width), "height": float(pdf.pages[page - 1].height)}
    rows = [{"text": w["text"], "x0": round(w["x0"], 1), "top": round(w["top"], 1),
             "x1": round(w["x1"], 1), "bottom": round(w["bottom"], 1)} for w in words[:limit]]
    return {"resource": resource_id, "page": page, "page_size": size, "words": rows,
            "truncated": len(words) > limit}


def render_page_image(project: Project, resource_id: str, page: int, resolution: int = 110) -> dict:
    """Render a PDF page to PNG (for visual reading of scanned pages or maps)."""
    resource = project.resource(resource_id)
    directory = project.derived_dir(resource_id) / "images"
    directory.mkdir(parents=True, exist_ok=True)
    if resource.media_type and resource.media_type.startswith("image/"):
        return {"resource": resource_id, "path": resource.path, "media_type": resource.media_type}
    path = directory / f"p{page:04d}_{resolution}dpi.png"
    if not path.exists():
        with _pdf(project, resource_id) as pdf:
            if not 1 <= page <= len(pdf.pages):
                raise ProjectError(f"Page {page} out of range 1..{len(pdf.pages)}")
            pdf.pages[page - 1].to_image(resolution=resolution).save(str(path), format="PNG")
    return {"resource": resource_id, "page": page, "path": project.rel(path), "media_type": "image/png"}
