"""Rebuildable SQLite index (.taxmap/index.sqlite) for search and provenance.

Files in the project are the source of truth; this index is a cache. It is rebuilt
automatically whenever the fingerprint of the registry, model files, or extracted
text changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from taxmap.extract import extract_text
from taxmap.project import Project

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE resources(id TEXT PRIMARY KEY, kind TEXT, title TEXT, path TEXT, origin_url TEXT,
                       media_type TEXT, tags TEXT, notes TEXT, pages INTEGER);
CREATE TABLE entities(kind TEXT, id TEXT, title TEXT, path TEXT, PRIMARY KEY(kind, id));
CREATE TABLE evidence(entity_kind TEXT, entity_id TEXT, field TEXT, resource_id TEXT, page INTEGER,
                      locator TEXT, quote TEXT);
CREATE INDEX evidence_resource ON evidence(resource_id);
CREATE INDEX evidence_entity ON evidence(entity_kind, entity_id);
CREATE VIRTUAL TABLE docs USING fts5(text, title, kind UNINDEXED, ref UNINDEXED, page UNINDEXED,
                                     tokenize='porter unicode61');
"""


def _db_path(project: Project) -> Path:
    return project.cache_dir / "index.sqlite"


def fingerprint(project: Project) -> str:
    digest = hashlib.sha256()
    candidates = [project.root / "resources.yaml", project.root / "taxmap.yaml"]
    if project.model_dir.exists():
        candidates += sorted(p for p in project.model_dir.rglob("*") if p.is_file())
    derived = project.root / "derived"
    if derived.exists():
        candidates += sorted(derived.glob("*/meta.json"))
    for path in candidates:
        if path.exists():
            stat = path.stat()
            digest.update(f"{path}|{stat.st_mtime_ns}|{stat.st_size}\n".encode())
    return digest.hexdigest()


def _connect(project: Project) -> sqlite3.Connection:
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(_db_path(project))


def _evidence_rows(kind: str, entity_id: str, field: str, evidence) -> list[tuple]:
    return [(kind, entity_id, field, e.resource, e.page, e.locator, e.quote) for e in evidence]


def rebuild(project: Project, extract_missing: bool = True) -> dict:
    """Recreate the index from files. Optionally extracts text for resources lacking it."""
    path = _db_path(project)
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    warnings: list[str] = []
    page_count = 0
    with closing(_connect(project)) as db, db:
        db.executescript(SCHEMA)
        for resource in project.resources():
            db.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?)", (
                resource.id, resource.kind, resource.title, resource.path, resource.origin_url,
                resource.media_type, json.dumps(resource.tags), resource.notes, resource.pages))
            meta_text = " ".join(filter(None, [resource.title, resource.notes, " ".join(resource.tags),
                                                resource.origin_url]))
            db.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (meta_text, resource.title, "resource",
                                                               resource.id, None))
            if not resource.path:
                continue
            meta = project.derived_dir(resource.id) / "meta.json"
            # Plain-text resources (notes, chats, saved web pages) are cheap to extract, so they are
            # always made searchable; PDFs and other documents only when extract_missing is set.
            cheap = (resource.media_type or "").startswith("text/") or resource.media_type == "application/json"
            if not meta.exists() and (extract_missing or cheap) and resource.kind != "image":
                try:
                    extract_text(project, resource.id)
                except Exception as error:
                    warnings.append(f"{resource.id}: {error}")
            pages_dir = project.derived_dir(resource.id) / "pages"
            for page_file in sorted(pages_dir.glob("*.txt")) if pages_dir.exists() else []:
                text = page_file.read_text(encoding="utf-8")
                if text.strip():
                    db.execute("INSERT INTO docs VALUES (?,?,?,?,?)",
                               (text, resource.title, "page", resource.id, int(page_file.stem)))
                    page_count += 1
        for source in project.sources():
            text = " ".join(filter(None, [source.title, source.jurisdiction, source.district, source.category,
                                          source.notes, source.finding, source.evidence_location, source.url]))
            db.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (text, source.title, "source", source.id, None))
            db.execute("INSERT INTO entities VALUES (?,?,?,?)", ("source", source.id, source.title,
                                                                 "model/sources.csv"))
            if source.resource:
                db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (
                    "source", source.id, "source", source.resource, None, source.evidence_location, None))
        evidence: list[tuple] = []
        for district in project.districts():
            db.execute("INSERT INTO entities VALUES (?,?,?,?)", ("district", district.id, district.name,
                                                                 f"model/districts/{district.id}.yaml"))
            db.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (
                " ".join(filter(None, [district.name, district.description, *(z.name for z in district.zones)])),
                district.name, "district", district.id, None))
            evidence += _evidence_rows("district", district.id, "district", district.evidence)
            for zone in district.zones:
                evidence += _evidence_rows("district", district.id, f"zone:{zone.id}", zone.evidence)
        for formula in project.formulas():
            db.execute("INSERT INTO entities VALUES (?,?,?,?)", ("formula", formula.id, formula.name,
                                                                 f"model/formulas/{formula.id}.yaml"))
            text = " ".join(filter(None, [formula.name, formula.description, formula.expression,
                                          *(i.label for i in formula.inputs), *formula.caveats]))
            db.execute("INSERT INTO docs VALUES (?,?,?,?,?)", (text, formula.name, "formula", formula.id, None))
            evidence += _evidence_rows("formula", formula.id, "formula", formula.evidence)
            for name, constant in formula.constants.items():
                evidence += _evidence_rows("formula", formula.id, f"constant:{name}", constant.evidence)
            for name, lookup in formula.lookups.items():
                evidence += _evidence_rows("formula", formula.id, f"lookup:{name}", lookup.evidence)
        for roll in project.rolls():
            db.execute("INSERT INTO entities VALUES (?,?,?,?)", ("roll", roll.id, roll.id,
                                                                 f"model/rolls/{roll.id}.yaml"))
            evidence += _evidence_rows("roll", roll.id, "roll", roll.evidence)
        notes_dir = project.model_dir / "notes"
        for note in sorted(notes_dir.glob("*.md")) if notes_dir.exists() else []:
            db.execute("INSERT INTO docs VALUES (?,?,?,?,?)",
                       (note.read_text(encoding="utf-8"), note.stem, "note", note.name, None))
        db.executemany("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", evidence)
        db.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint(project),))
    return {"index": project.rel(path), "pages_indexed": page_count, "warnings": warnings}


def ensure(project: Project) -> None:
    path = _db_path(project)
    if path.exists():
        try:
            with closing(sqlite3.connect(path)) as db:
                row = db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            if row and row[0] == fingerprint(project):
                return
        except sqlite3.DatabaseError:
            pass
    rebuild(project, extract_missing=False)


def _fts_query(query: str) -> str:
    tokens = re.findall(r'"[^"]+"|[\w$.\-]+', query)
    return " ".join(t if t.startswith('"') else '"' + t.replace('"', "") + '"' for t in tokens)


def search(project: Project, query: str, kinds: list[str] | None = None, limit: int = 20,
           raw: bool = False) -> list[dict]:
    """Full-text search over document pages, sources, model entities and notes."""
    ensure(project)
    match = query if raw else _fts_query(query)
    if not match.strip():
        return []
    sql = ("SELECT kind, ref, page, title, snippet(docs, 0, '[', ']', ' … ', 24), bm25(docs) "
           "FROM docs WHERE docs MATCH ?")
    params: list = [match]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params += kinds
    sql += " ORDER BY bm25(docs) LIMIT ?"
    params.append(limit)
    with closing(sqlite3.connect(_db_path(project))) as db:
        rows = db.execute(sql, params).fetchall()
    return [{"kind": k, "ref": r, "page": p, "title": t, "snippet": s, "score": round(-b, 3)}
            for k, r, p, t, s, b in rows]


def evidence_for(project: Project, resource_id: str | None = None, entity_kind: str | None = None,
                 entity_id: str | None = None) -> list[dict]:
    """Provenance lookups: what cites a resource, or what a model entity cites."""
    ensure(project)
    clauses, params = [], []
    if resource_id:
        clauses.append("resource_id = ?")
        params.append(resource_id)
    if entity_kind:
        clauses.append("entity_kind = ?")
        params.append(entity_kind)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with closing(sqlite3.connect(_db_path(project))) as db:
        rows = db.execute(f"SELECT entity_kind, entity_id, field, resource_id, page, locator, quote "
                          f"FROM evidence{where} ORDER BY entity_kind, entity_id", params).fetchall()
    keys = ("entity_kind", "entity_id", "field", "resource", "page", "locator", "quote")
    return [{k: v for k, v in zip(keys, row) if v is not None} for row in rows]
