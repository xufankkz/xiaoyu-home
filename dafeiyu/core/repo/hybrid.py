"""FTS5/BM25 and persisted local sentence vectors for memory and knowledge.

SQL triggers mark changed source rows. Index synchronization is transactional,
including for direct SQL writers, and never changes an original record.
"""
from __future__ import annotations

from array import array
import hashlib
import re

from .. import db
from ..entity import now_iso
from ..memory import embed, semantic


def index_tokens(text: str) -> str:
    out = []
    for word in re.findall(r"[\u3400-\u9fff]+|[a-z0-9_]+", str(text).lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            out.extend(word)
            out.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            out.append(word)
    return " ".join(out)


def initialize(conn) -> None:
    if not conn.execute("SELECT 1 FROM meta WHERE key='hybrid_index_v1'").fetchone():
        conn.execute("INSERT OR IGNORE INTO search_dirty SELECT id,'memory' FROM memory_item")
        conn.execute("INSERT OR IGNORE INTO search_dirty SELECT id,'knowledge' FROM knowledge_item")
        conn.execute("INSERT INTO meta(key,value) VALUES('hybrid_index_v1','1')")


def flush() -> int:
    count = 0
    with db.write() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        dirty = conn.execute("SELECT owner,kind FROM search_dirty").fetchall()
        for owner, kind in dirty:
            if kind not in {"memory", "knowledge"}:
                continue
            table = "memory_fts" if kind == "memory" else "knowledge_fts"
            if kind == "memory":
                row = conn.execute("SELECT content,summary,skeleton,concepts,layer,tombstone,level FROM memory_item WHERE id=?", (owner,)).fetchone()
                alive = row is not None and not row["tombstone"] and row["level"] < 3
                text = "\n".join(str(row[k] or "") for k in ("summary", "content", "skeleton")) if alive else ""
                labels = str(row["concepts"] or "") if alive else ""
            else:
                row = conn.execute("SELECT name,content,concepts FROM knowledge_item WHERE id=?", (owner,)).fetchone()
                alive = row is not None
                text = f"{row['name']}\n{row['content']}" if alive else ""
                labels = f"{row['name']} {row['concepts']}" if alive else ""
            fingerprint = hashlib.sha256((text + "\n" + labels).encode()).hexdigest()
            previous = conn.execute("SELECT fingerprint FROM search_document WHERE owner=? AND kind=?", (owner, kind)).fetchone()
            if alive and previous and previous["fingerprint"] == fingerprint:
                conn.execute("DELETE FROM search_dirty WHERE owner=? AND kind=?", (owner, kind))
                count += 1
                continue
            conn.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
            conn.execute("DELETE FROM search_document WHERE owner=? AND kind=?", (owner, kind))
            conn.execute("DELETE FROM embedding WHERE owner=? AND owner_kind=? AND backend=?", (owner, kind, semantic.MODEL_ID))
            if alive:
                conn.execute("INSERT INTO search_document(owner,kind,text,fingerprint) VALUES(?,?,?,?)", (owner, kind, text, fingerprint))
                conn.execute(f"INSERT INTO {table}(owner,body,labels) VALUES(?,?,?)", (owner, index_tokens(text), index_tokens(labels)))
            conn.execute("DELETE FROM search_dirty WHERE owner=? AND kind=?", (owner, kind))
            count += 1
    return count


def _vectors(query: str, kind: str) -> tuple[dict[str, float], str]:
    rows = db.q("SELECT d.owner,d.text,d.fingerprint,e.vec FROM search_document d LEFT JOIN embedding e "
                "ON e.owner=d.owner AND e.owner_kind=d.kind AND e.backend=? WHERE d.kind=? ORDER BY d.owner",
                (semantic.MODEL_ID, kind))
    if not rows:
        return {}, semantic.status()["backend"]
    if not semantic.available():
        return {r["owner"]: embed.similarity(query, r["text"]) for r in rows}, "lexical-bigram-fallback"
    try:
        return _encode_vectors(query, kind, rows), semantic.MODEL_ID
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        semantic.note_error(exc)
        return {r["owner"]: embed.similarity(query, r["text"]) for r in rows}, "lexical-bigram-fallback"


def _encode_vectors(query: str, kind: str, rows) -> dict[str, float]:
    # CPU batches bound peak memory; vectors contain no executable serialization.
    qvec = semantic.encode(query)
    vectors = {r["owner"]: list(array("f", r["vec"])) for r in rows if r["vec"] is not None}
    missing = [r for r in rows if r["vec"] is None]
    for offset in range(0, len(missing), 16):
        batch = missing[offset:offset + 16]
        encoded = semantic.encode_batch([r["text"] for r in batch])
        with db.write() as conn:
            for row, vec in zip(batch, encoded):
                vectors[row["owner"]] = vec
                conn.execute("INSERT OR REPLACE INTO embedding(owner,owner_kind,backend,dim,vec,t) "
                             "SELECT ?,?,?,?, ?,? WHERE EXISTS(SELECT 1 FROM search_document WHERE owner=? AND kind=? AND fingerprint=?)",
                             (row["owner"], kind, semantic.MODEL_ID, len(vec), array("f", vec).tobytes(), now_iso(), row["owner"], kind, row["fingerprint"]))
    return {owner: max(0.0, sum(a*b for a, b in zip(qvec, vec))) for owner, vec in vectors.items()}


def rrf(lanes: list[tuple[list[str], float]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ids, weight in lanes:
        for rank, owner in enumerate(dict.fromkeys(ids), 1):
            scores[owner] = scores.get(owner, 0.0) + max(0.0, weight) / (k + rank)
    return scores


def search(query: str, kind: str = "memory", limit: int = 100) -> dict:
    if kind not in {"memory", "knowledge"}:
        raise ValueError("Unsupported hybrid index kind")
    flush()
    tokens = list(dict.fromkeys(index_tokens(query).split()))[:64]
    table = "memory_fts" if kind == "memory" else "knowledge_fts"
    kw = []
    if tokens:
        match = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
        kw = [r["owner"] for r in db.q(f"SELECT owner FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table},0,1,3),owner LIMIT ?", (match, max(1, int(limit))))]
    vector_scores, backend = _vectors(query, kind) if query.strip() else ({}, semantic.status()["backend"])
    semantic_ids = sorted((i for i, s in vector_scores.items() if s > 0.05), key=lambda i: (-vector_scores[i], i))[:limit]
    scores = rrf([(kw, 0.45), (semantic_ids, 0.55)])
    max_score = 1.0 / 61
    ranked = [{"id": owner, "score": round(score / max_score, 6),
               "semantic": round(vector_scores.get(owner, 0.0), 5), "keyword_hit": owner in kw}
              for owner, score in sorted(scores.items(), key=lambda p: (-p[1], p[0]))[:limit]]
    return {"ranked": ranked, "backend": backend, "fusion": "weighted-rrf", "fts": "fts5-bm25"}


def status() -> dict:
    from .. import settings, slang
    return {**semantic.status(), "fusion": "weighted-rrf", "fts": "fts5-bm25",
            "lookup": slang.status(), "reflection": settings.read_state("reflection.json", {}),
            "indexed": {k: int(db.scalar("SELECT COUNT(*) FROM search_document WHERE kind=?", (k,), 0)) for k in ("memory", "knowledge")},
            "pending": int(db.scalar("SELECT COUNT(*) FROM search_dirty", (), 0))}
