"""A role: persistent bug corpus retrieval and approved-record upserts."""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # Unit tests can run before project dependencies are installed.
    def load_dotenv() -> bool:
        return False

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

load_dotenv()

BASE_URL = os.getenv("FURIOSA_BASE_URL", "https://endpoint.access.furiosa.dev/v1").rstrip("/")
EMBEDDING_MODEL = os.getenv("FURIOSA_EMBEDDING_MODEL", "furiosa-ai/Qwen3-Embedding-8B")
RERANKER_MODEL = os.getenv("FURIOSA_RERANKER_MODEL", "furiosa-ai/Qwen3-Reranker-8B")
CORPUS_PATH = Path(__file__).with_name("bugs.json")

_cases: list[dict[str, Any]] | None = None
_embeddings: list[list[float]] | None = None


def build_query(extracted: dict, context: str = "") -> str:
    """Build a retrieval query from error text, code, and optional context."""
    return " ".join(
        value.strip()
        for value in (
            str(extracted.get("error_text", "")),
            str(extracted.get("code_snippet", "")),
            context,
        )
        if value and value.strip()
    )


def build_document(case: dict) -> str:
    """Build the text that represents a BugCase in the embedding index."""
    return " ".join(
        str(case.get(key, "")).strip()
        for key in ("error_message", "context", "root_cause", "environment")
        if str(case.get(key, "")).strip()
    )


def _api_key(variable_name: str) -> str:
    key = os.getenv(variable_name)
    if not key:
        raise RuntimeError(f"{variable_name}를 .env에 설정해야 RAG API를 호출할 수 있습니다.")
    return key


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if OpenAI is None:
        raise RuntimeError("openai 패키지가 필요합니다. pip install -r requirements.txt를 실행하세요.")
    client = OpenAI(base_url=BASE_URL, api_key=_api_key("FURIOSA_EMBEDDING_API_KEY"))
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [list(item.embedding) for item in response.data]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _rerank(query: str, documents: list[str]) -> list[dict[str, Any]]:
    """Call Furiosa's OpenAI-compatible rerank endpoint.

    The service returns ``relevance_score``; this module maps it to the team's
    public ``reranker_score`` contract in ``search_bug_corpus``.
    """
    if not documents:
        return []
    payload = json.dumps(
        {"model": RERANKER_MODEL, "query": query, "documents": documents, "top_n": len(documents)}
    ).encode("utf-8")
    request = Request(
        f"{BASE_URL}/rerank",
        data=payload,
        headers={
            "Authorization": f"Bearer {_api_key('FURIOSA_RERANKER_API_KEY')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"리랭커 API 요청 실패: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"리랭커 API 연결 실패: {exc.reason}") from exc

    results = body.get("results", body.get("data", []))
    return [item for item in results if isinstance(item, dict)]


def _load_cases() -> list[dict[str, Any]]:
    if not CORPUS_PATH.exists():
        return []
    with CORPUS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{CORPUS_PATH.name}은 BugCase 목록(JSON 배열)이어야 합니다.")
    return data


def _rebuild_index() -> None:
    global _cases, _embeddings
    _cases = _load_cases()
    _embeddings = _embed_texts([build_document(case) for case in _cases])
    if len(_embeddings) != len(_cases):
        raise RuntimeError("임베딩 API가 코퍼스 문서 수와 다른 개수의 벡터를 반환했습니다.")


def _ensure_index() -> tuple[list[dict[str, Any]], list[list[float]]]:
    if _cases is None or _embeddings is None:
        _rebuild_index()
    return _cases or [], _embeddings or []


def search_bug_corpus(query: str, top_k: int = 5) -> list[dict]:
    """Return top BugCases with a 0-1 ``reranker_score`` for each result."""
    if top_k < 1:
        return []
    query = query.strip()
    if not query:
        return []

    cases, embeddings = _ensure_index()
    if not cases:
        return []

    query_embedding = _embed_texts([query])[0]
    candidate_count = min(len(cases), max(top_k * 3, top_k))
    candidates = sorted(
        range(len(cases)),
        key=lambda index: _cosine_similarity(query_embedding, embeddings[index]),
        reverse=True,
    )[:candidate_count]

    reranked = _rerank(query, [build_document(cases[index]) for index in candidates])
    results: list[dict] = []
    for item in reranked:
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            continue
        case = dict(cases[candidates[index]])
        case["reranker_score"] = float(item.get("relevance_score", item.get("score", 0.0)))
        results.append(case)
    return sorted(results, key=lambda case: case["reranker_score"], reverse=True)[:top_k]


def _write_cases(cases: list[dict[str, Any]]) -> None:
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=CORPUS_PATH.parent, delete=False, suffix=".json"
    ) as temporary:
        json.dump(cases, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(CORPUS_PATH)


def _next_id(cases: list[dict[str, Any]]) -> str:
    numbers = [
        int(str(case.get("id", "")).split("_")[-1])
        for case in cases
        if str(case.get("id", "")).startswith("bug_") and str(case.get("id", "")).split("_")[-1].isdigit()
    ]
    return f"bug_{max(numbers, default=0) + 1:03d}"


def upsert_bug_case(record: dict, match_type: str, matched_case_id: str | None) -> None:
    """Persist an approved result and rebuild the in-memory embedding index.

    Only an explicitly confirmed match updates an existing case. Possible and
    new findings intentionally become independent BugCases for later review.
    """
    global _cases, _embeddings
    cases = _load_cases()
    now = datetime.now(timezone.utc).isoformat()

    if match_type == "confirmed" and matched_case_id:
        existing = next((case for case in cases if case.get("id") == matched_case_id), None)
        if existing is not None:
            existing["occurrence_count"] = int(existing.get("occurrence_count", 0)) + 1
            existing["last_seen_at"] = now
            _write_cases(cases)
            _rebuild_index()
            return

    new_case = dict(record)
    new_case["id"] = _next_id(cases)
    new_case["occurrence_count"] = int(new_case.get("occurrence_count", 1) or 1)
    new_case["first_seen_at"] = new_case.get("first_seen_at") or now
    new_case["last_seen_at"] = now
    cases.append(new_case)
    _write_cases(cases)
    _rebuild_index()
