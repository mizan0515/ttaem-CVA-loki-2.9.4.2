"""Free local vector index over timed summary evidence.

This is the first local embedding/index layer for normal summary production. It
uses a deterministic hashing-vectorizer over chunk text while the text is still
available in memory, then binds every vector row back to `timed_evidence_item.v1`
metadata. Serialized metadata stays raw-free: no transcript/chat text, no token
strings, and no private absolute paths.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any


SCHEMA_VERSION = "summary_local_vector_index.v1"
EMBEDDING_MODEL = "local_hashing_word_v1"
RETRIEVAL_MODE = "local_free_hash_vector_cosine"

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _items_by_chunk(manifest: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    if not isinstance(manifest, dict):
        return {}
    rows = {}
    for item in manifest.get("items") or []:
        if isinstance(item, dict):
            rows[_as_int(item.get("chunk_index"))] = item
    return rows


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(str(text or ""))]


def _dim(token: str, dimensions: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8", errors="replace")).digest()
    return int.from_bytes(digest[:4], "big") % max(1, dimensions)


def _vectorize(text: str, *, dimensions: int, max_features: int) -> tuple[list[dict[str, float | int]], float]:
    counts: dict[int, float] = {}
    for token in _tokens(text):
        dim = _dim(token, dimensions)
        counts[dim] = counts.get(dim, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    if norm <= 0:
        return [], 0.0
    rows = [
        {"dim": dim, "weight": round(value / norm, 6)}
        for dim, value in sorted(counts.items(), key=lambda row: (-row[1], row[0]))[: max(1, max_features)]
    ]
    return rows, round(norm, 6)


def build_summary_local_vector_index(
    *,
    chunks: list[dict[str, Any]],
    timed_evidence_manifest: dict[str, Any] | None,
    dimensions: Any = 256,
    max_features_per_item: Any = 32,
) -> dict[str, Any]:
    """Build a raw-free local vector index bound to timed evidence refs."""

    dims = max(16, _as_int(dimensions, 256))
    max_features = max(4, _as_int(max_features_per_item, 32))
    by_chunk = _items_by_chunk(timed_evidence_manifest)
    index_items: list[dict[str, Any]] = []
    empty_text_count = 0
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_index = _as_int(chunk.get("index"))
        evidence = by_chunk.get(chunk_index)
        if not evidence:
            continue
        vector, vector_norm = _vectorize(
            str(chunk.get("text") or ""),
            dimensions=dims,
            max_features=max_features,
        )
        if not vector:
            empty_text_count += 1
        index_items.append(
            {
                "evidence_id": evidence.get("evidence_id") or "",
                "chunk_index": chunk_index,
                "start_sec": evidence.get("start_sec"),
                "end_sec": evidence.get("end_sec"),
                "text_hash": evidence.get("text_hash") or "",
                "source_ref": evidence.get("source_ref") or "",
                "route_ref": evidence.get("route_ref") or "",
                "path_ref": evidence.get("path_ref") or "",
                "token_count": _as_int(evidence.get("token_count")),
                "vector_norm": vector_norm,
                "feature_count": len(vector),
                "sparse_vector": vector,
            }
        )
    vectorized_count = sum(1 for item in index_items if item.get("feature_count"))
    return {
        "schema_version": SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "retrieval_mode": RETRIEVAL_MODE,
        "dimensions": dims,
        "max_features_per_item": max_features,
        "metadata_binding": "timed_evidence_item.v1:evidence_id",
        "raw_content_included": False,
        "token_text_included": False,
        "summary": {
            "item_count": len(index_items),
            "vectorized_count": vectorized_count,
            "empty_text_count": empty_text_count,
            "metadata_bound_count": len(index_items),
        },
        "items": index_items,
    }


def compact_summary_local_vector_index(index: dict[str, Any] | None) -> dict[str, Any]:
    """Return metadata-safe index summary without sparse vector rows."""

    if not isinstance(index, dict):
        return {}
    items = [item for item in (index.get("items") or []) if isinstance(item, dict)]
    return {
        "schema_version": index.get("schema_version"),
        "embedding_model": index.get("embedding_model"),
        "retrieval_mode": index.get("retrieval_mode"),
        "dimensions": _as_int(index.get("dimensions")),
        "max_features_per_item": _as_int(index.get("max_features_per_item")),
        "metadata_binding": index.get("metadata_binding"),
        "raw_content_included": bool(index.get("raw_content_included") or False),
        "token_text_included": bool(index.get("token_text_included") or False),
        "summary": index.get("summary") if isinstance(index.get("summary"), dict) else {},
        "items": [
            {
                "evidence_id": item.get("evidence_id") or "",
                "chunk_index": _as_int(item.get("chunk_index")),
                "start_sec": item.get("start_sec"),
                "end_sec": item.get("end_sec"),
                "text_hash": item.get("text_hash") or "",
                "source_ref": item.get("source_ref") or "",
                "route_ref": item.get("route_ref") or "",
                "path_ref": item.get("path_ref") or "",
                "token_count": _as_int(item.get("token_count")),
                "vector_norm": item.get("vector_norm"),
                "feature_count": _as_int(item.get("feature_count")),
            }
            for item in items
        ],
    }


def query_summary_local_vector_index(
    index: dict[str, Any] | None,
    query: str,
    *,
    top_k: Any = 3,
) -> list[dict[str, Any]]:
    """Return top evidence refs by local hashed-vector cosine similarity."""

    if not isinstance(index, dict) or not query:
        return []
    dims = max(16, _as_int(index.get("dimensions"), 256))
    max_features = max(4, _as_int(index.get("max_features_per_item"), 32))
    query_vector, _query_norm = _vectorize(query, dimensions=dims, max_features=max_features)
    query_weights = {int(row["dim"]): float(row["weight"]) for row in query_vector}
    if not query_weights:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in index.get("items") or []:
        if not isinstance(item, dict):
            continue
        vector = {
            int(row.get("dim")): float(row.get("weight"))
            for row in (item.get("sparse_vector") or [])
            if isinstance(row, dict)
        }
        score = sum(query_weights.get(dim, 0.0) * weight for dim, weight in vector.items())
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda row: (-row[0], _as_int(row[1].get("start_sec")), str(row[1].get("evidence_id") or "")))
    limit = max(1, _as_int(top_k, 3))
    return [
        {
            "evidence_id": item.get("evidence_id") or "",
            "rank": rank,
            "score": round(score, 6),
            "match_reason": RETRIEVAL_MODE,
            "start_sec": item.get("start_sec"),
            "end_sec": item.get("end_sec"),
            "text_hash": item.get("text_hash") or "",
            "source_ref": item.get("source_ref") or "",
            "route_ref": item.get("route_ref") or "",
            "path_ref": item.get("path_ref") or "",
            "raw_content_included": False,
        }
        for rank, (score, item) in enumerate(scored[:limit], start=1)
    ]


__all__ = [
    "EMBEDDING_MODEL",
    "RETRIEVAL_MODE",
    "SCHEMA_VERSION",
    "build_summary_local_vector_index",
    "compact_summary_local_vector_index",
    "query_summary_local_vector_index",
]
