"""Notion 지식 DB 조회 및 URL 기준 upsert."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict
from typing import Any, Iterable

import requests

import config
from crawler import CrawlDocument

logger = logging.getLogger(__name__)


class NotionAPIError(RuntimeError):
    pass


def notion_error_message(exc: Exception, database_label: str = "Notion DB") -> str:
    """관리자 화면에 노출할 수 있는 실행 가능한 Notion 오류 문구를 반환한다."""
    message = str(exc)
    if "object_not_found" in message or "Could not find database" in message:
        return (
            f"{database_label}에 접근할 수 없습니다. "
            "Notion에서 해당 데이터베이스를 연 뒤 우측 상단 ··· → 연결(Connections)에서 "
            'Integration "장민호부장-api"를 추가하고, Render 환경변수의 DB ID가 실제 데이터베이스 ID와 '
            "일치하는지 확인해 주세요."
        )
    if "unauthorized" in message.lower() or "401" in message:
        return "NOTION_TOKEN이 유효하지 않습니다. Render의 NOTION_TOKEN 값을 다시 확인해 주세요."
    if "NOTION_TOKEN" in message:
        return "Render 환경변수에 NOTION_TOKEN이 설정되지 않았습니다."
    return message


def normalize_id(raw: str) -> str:
    value = (raw or "").strip()
    db_link_match = re.search(r"(?:/p/DB-|/p/)([0-9a-fA-F]{32})(?:[/?#]|$)", value)
    if db_link_match:
        return db_link_match.group(1)
    compact = value.replace("-", "")
    matches = re.findall(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])", compact)
    return matches[-1] if matches else compact


def rich_text(text: str, limit: int = 1900) -> list[dict[str, Any]]:
    value = (text or "").strip()[:limit]
    return [{"type": "text", "text": {"content": value}}] if value else []


class NotionClient:
    def __init__(self, token: str = config.NOTION_TOKEN) -> None:
        self.token = token
        self.base_url = "https://api.notion.com/v1"
        self.session = requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise NotionAPIError("NOTION_TOKEN이 설정되지 않았습니다.")
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": config.NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error = ""
        for attempt in range(1, 5):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    json=payload,
                    timeout=40,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                time.sleep(attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = response.text[:1000]
                retry_after = float(response.headers.get("retry-after", attempt))
                time.sleep(max(retry_after, attempt))
                continue
            if response.status_code >= 400:
                raise NotionAPIError(f"Notion API 오류 ({response.status_code}): {response.text[:1500]}")
            return response.json() if response.content else {}
        raise NotionAPIError(f"Notion API 재시도 실패: {last_error}")

    def database(self, database_id: str) -> dict[str, Any]:
        return self.request("GET", f"/databases/{normalize_id(database_id)}")

    def validate_database(self, database_id: str, label: str) -> dict[str, Any]:
        try:
            return self.database(database_id)
        except Exception as exc:
            raise NotionAPIError(notion_error_message(exc, label)) from exc

    def query_all(
        self,
        database_id: str,
        *,
        filter_payload: dict[str, Any] | None = None,
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor = None
        while True:
            payload: dict[str, Any] = {"page_size": min(page_size, 100)}
            if cursor:
                payload["start_cursor"] = cursor
            if filter_payload:
                payload["filter"] = filter_payload
            if sorts:
                payload["sorts"] = sorts
            data = self.request("POST", f"/databases/{normalize_id(database_id)}/query", payload)
            results.extend(data.get("results") or [])
            if limit and len(results) >= limit:
                return results[:limit]
            if not data.get("has_more") or not data.get("next_cursor"):
                return results
            cursor = data["next_cursor"]

    def find_by_url(self, database_id: str, url: str) -> dict[str, Any] | None:
        pages = self.query_all(
            database_id,
            filter_payload={"property": "원본URL", "url": {"equals": url}},
            limit=1,
        )
        return pages[0] if pages else None

    @staticmethod
    def _property_text(prop: dict[str, Any]) -> str:
        prop_type = prop.get("type")
        if prop_type in {"title", "rich_text"}:
            return "".join(x.get("plain_text", "") for x in prop.get(prop_type, []))
        if prop_type == "url":
            return prop.get("url") or ""
        if prop_type == "select":
            return (prop.get("select") or {}).get("name", "")
        if prop_type == "date":
            return (prop.get("date") or {}).get("start", "")
        return ""

    def _properties(self, doc: CrawlDocument, status: str) -> dict[str, Any]:
        props: dict[str, Any] = {
            "제목": {"title": rich_text(doc.title, 300)},
            "카테고리": {"select": {"name": (doc.category or "기타")[:100]}},
            "본문": {"rich_text": rich_text(doc.body)},
            "요약": {"rich_text": rich_text(doc.summary)},
            "원본URL": {"url": doc.source_url},
            "수집일": {"date": {"start": doc.collected_at}},
            "키워드": {"multi_select": [{"name": word[:100]} for word in doc.keywords[:15]]},
            "콘텐츠해시": {"rich_text": rich_text(doc.content_hash, 100)},
            "상태": {"select": {"name": status}},
            "검색용텍스트": {"rich_text": rich_text(doc.search_text)},
        }
        if doc.published_at:
            props["게시일"] = {"date": {"start": doc.published_at}}
        return props

    @staticmethod
    def _body_blocks(doc: CrawlDocument) -> list[dict[str, Any]]:
        text = doc.body
        chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)][:45]
        blocks: list[dict[str, Any]] = []
        if doc.attachments:
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": rich_text("첨부파일")},
                }
            )
            for url in doc.attachments[:20]:
                blocks.append(
                    {
                        "object": "block",
                        "type": "bookmark",
                        "bookmark": {"url": url},
                    }
                )
        if chunks:
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": rich_text("수집 본문")},
                }
            )
            blocks.extend(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich_text(chunk)},
                }
                for chunk in chunks
            )
        return blocks

    def upsert_document(self, doc: CrawlDocument, database_id: str = config.NOTION_KNOWLEDGE_DB_ID) -> str:
        existing = self.find_by_url(database_id, doc.source_url)
        if existing:
            old_hash = self._property_text((existing.get("properties") or {}).get("콘텐츠해시", {}))
            status = "유지" if old_hash == doc.content_hash else "변경"
            self.request("PATCH", f"/pages/{existing['id']}", {"properties": self._properties(doc, status)})
            if status == "변경":
                update_blocks = [
                    {
                        "object": "block",
                        "type": "divider",
                        "divider": {},
                    },
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {"rich_text": rich_text(f"변경 수집본 · {doc.collected_at[:19]}")},
                    },
                    *self._body_blocks(doc),
                ]
                self.request("PATCH", f"/blocks/{existing['id']}/children", {"children": update_blocks})
            return status
        payload = {
            "parent": {"database_id": normalize_id(database_id)},
            "properties": self._properties(doc, "신규"),
            "children": self._body_blocks(doc),
        }
        self.request("POST", "/pages", payload)
        return "신규"

    def upsert_many(self, documents: Iterable[CrawlDocument]) -> dict[str, int]:
        counts = {"신규": 0, "변경": 0, "유지": 0, "실패": 0}
        for doc in documents:
            try:
                status = self.upsert_document(doc)
                counts[status] += 1
            except Exception as exc:
                counts["실패"] += 1
                logger.exception("Notion 적재 실패 url=%s error=%s", doc.source_url, exc)
        return counts

    def knowledge_documents(self, limit: int | None = None) -> list[dict[str, Any]]:
        pages = self.query_all(config.NOTION_KNOWLEDGE_DB_ID, limit=limit)
        documents = []
        for page in pages:
            props = page.get("properties") or {}
            get = lambda name: self._property_text(props.get(name, {}))
            keywords_prop = props.get("키워드", {})
            keywords = [x.get("name", "") for x in keywords_prop.get("multi_select", [])]
            documents.append(
                {
                    "page_id": page.get("id", ""),
                    "title": get("제목"),
                    "category": get("카테고리"),
                    "body": get("본문"),
                    "summary": get("요약"),
                    "source_url": get("원본URL"),
                    "published_at": get("게시일"),
                    "collected_at": get("수집일"),
                    "keywords": keywords,
                    "content_hash": get("콘텐츠해시"),
                    "status": get("상태"),
                    "search_text": get("검색용텍스트"),
                }
            )
        return documents

    def recent_knowledge(self, limit: int = 20) -> list[dict[str, Any]]:
        pages = self.query_all(
            config.NOTION_KNOWLEDGE_DB_ID,
            sorts=[{"property": "수집일", "direction": "descending"}],
            limit=limit,
        )
        documents = []
        for page in pages:
            props = page.get("properties") or {}
            get = lambda name: self._property_text(props.get(name, {}))
            documents.append(
                {
                    "title": get("제목"),
                    "category": get("카테고리"),
                    "source_url": get("원본URL"),
                    "collected_at": get("수집일"),
                    "status": get("상태"),
                }
            )
        return documents
