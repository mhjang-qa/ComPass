"""정확한 단답이 필요한 관리자 검증형 구조화 지식."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import config
from crawler import CrawlDocument

CURATED_PATH = config.BASE_DIR / "data" / "curated_knowledge.json"


def load_curated_items() -> list[dict[str, Any]]:
    try:
        payload = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in payload.get("items", []) if isinstance(item, dict) and item.get("enabled", True)]


def curated_documents() -> list[CrawlDocument]:
    documents: list[CrawlDocument] = []
    for item in load_curated_items():
        body = "\n".join(
            [
                f"의도: {item.get('intent', '')}",
                f"과목: {item.get('subject', '')}",
                f"시험종류: {item.get('assessment', '')}",
                f"적용기준: {item.get('validity', '')}",
                f"공식답변: {item.get('answer', '')}",
                f"근거설명: {item.get('note', '')}",
                f"추천그룹수: {len(item.get('recommendation_groups', []))}",
                f"구조화항목수: {len(item.get('structured_items', []))}",
            ]
        )
        document = CrawlDocument(
            title=item.get("title", "구조화 지식"),
            category=item.get("category", "구조화 지식"),
            body=body,
            source_url=item.get("source_url", config.CRAWL_START_URL),
            collected_at=item.get("updated_at", "2026-06-22T00:00:00+09:00"),
            published_at=item.get("published_at", ""),
        )
        document.normalized_items = [
            {**course, "group_name": group.get("group_name", "")}
            for group in item.get("recommendation_groups", [])
            for course in group.get("items", [])
        ] or item.get("structured_items", [])
        document.finalize()
        document.document_type = "검증지식"
        document.summary = item.get("answer", document.summary)
        document.keywords = item.get("keywords", document.keywords)
        document.search_text = " ".join(
            [document.title, document.category, document.summary, *document.keywords, document.body]
        )
        documents.append(document)
    return documents


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def match_curated(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    compact = _compact(question)
    prior = " ".join(
        str(item.get("content") or "")
        for item in (history or [])[-6:]
        if item.get("role") == "user" and _compact(item.get("content", "")) != compact
    )
    context = _compact(f"{prior} {question}")

    items = load_curated_items()
    # 현재 질문만으로 판별되는 명시적 의도를 먼저 처리하고, 모호한 후속 질문만 대화 문맥을 쓴다.
    for item in sorted(items, key=lambda value: bool(value.get("use_context", False))):
        match_all = [_compact(term) for term in item.get("match_all", [])]
        match_any = [_compact(term) for term in item.get("match_any", [])]
        exclude = [_compact(term) for term in item.get("exclude", [])]
        target = context if item.get("use_context", False) else compact
        if exclude and any(term in target for term in exclude):
            continue
        if match_all and not all(term in target for term in match_all):
            continue
        if match_any and not any(term in target for term in match_any):
            continue
        if match_all or match_any:
            return item
    return None
