"""질문·답변 통계를 Notion 통계 DB에 저장."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

import config
from chatbot import sanitize_input
from notion_client import NotionClient, normalize_id, rich_text

logger = logging.getLogger(__name__)


def record_interaction(question: str, result: dict[str, Any]) -> None:
    client = NotionClient()
    client.ensure_stats_schema()
    sources = result.get("sources") or []
    item_urls = [
        item.get("source_url") or item.get("fallback_url")
        for item in (result.get("items") or [])
    ]
    all_urls = [
        *(source.get("url", "") for source in sources),
        *(result.get("source_urls") or []),
        *item_urls,
    ]
    reference_urls = "\n".join(dict.fromkeys(url for url in all_urls if url))
    mode = result.get("mode", "SYSTEM")
    payload = {
        "parent": {"database_id": normalize_id(config.NOTION_STATS_DB_ID)},
        "properties": {
            "사용자질문": {"title": rich_text(sanitize_input(question), 500)},
            "session_id": {"rich_text": rich_text(str(result.get("session_id") or "")[:120], 120)},
            "request_id": {"rich_text": rich_text(str(result.get("request_id") or "")[:120], 120)},
            "llm_type": {"select": {"name": str(result.get("llm_type") or "none")[:100]}},
            "allow_llm": {"checkbox": bool(result.get("allow_llm"))},
            "requires_llm_confirmation": {"checkbox": bool(result.get("requires_llm_confirmation"))},
            "질문일시": {"date": {"start": datetime.now().astimezone().isoformat()}},
            "추출키워드": {
                "multi_select": [{"name": str(word)[:100]} for word in (result.get("keywords") or [])[:15]]
            },
            "검색결과유무": {"checkbox": bool(result.get("sources"))},
            "응답방식": {"select": {"name": mode if mode in {"DB검색", "LLM"} else "시스템"}},
            "답변내용": {"rich_text": rich_text(result.get("answer", ""))},
            "참조URL": {"rich_text": rich_text(reference_urls)},
            "응답시간": {"number": float(result.get("elapsed_ms") or 0)},
            "검색점수": {"number": float(result.get("score") or 0)},
            "실패사유": {"rich_text": rich_text(result.get("failure_reason", ""))},
            "응답유형": {"select": {"name": str(result.get("answer_type") or "text")[:100]}},
            "응답요약": {"rich_text": rich_text(result.get("summary", ""), 1000)},
            "표시항목수": {"number": min(len(result.get("items") or []), int(result.get("display_limit") or 3))},
        },
    }
    client.request("POST", "/pages", payload)


def record_interaction_async(question: str, result: dict[str, Any]) -> None:
    def worker() -> None:
        try:
            record_interaction(question, result)
        except Exception as exc:
            logger.warning("통계 저장 실패: %s", exc)

    threading.Thread(target=worker, daemon=True).start()


def recent_stats(limit: int = 30) -> list[dict[str, Any]]:
    client = NotionClient()
    client.ensure_stats_schema()
    pages = client.query_all(config.NOTION_STATS_DB_ID, limit=limit)
    rows = []
    for page in pages:
        props = page.get("properties") or {}
        row = {"id": page.get("id"), "url": page.get("url")}
        for name, prop in props.items():
            row[name] = client._property_text(prop)
            if prop.get("type") == "number":
                row[name] = prop.get("number")
            elif prop.get("type") == "checkbox":
                row[name] = prop.get("checkbox")
            elif prop.get("type") == "multi_select":
                row[name] = [item.get("name") for item in prop.get("multi_select", [])]
        rows.append(row)
    return rows
