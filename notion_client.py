"""Notion 지식 DB 조회 및 URL 기준 upsert."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from typing import Any, Iterable

import requests

import config
from crawler import CrawlDocument
from curated_knowledge import curated_documents

logger = logging.getLogger(__name__)

KNOWLEDGE_SCHEMA: dict[str, dict[str, Any]] = {
    "출처구분": {"select": {}},
    "출처명": {"rich_text": {}},
    "문서유형": {"select": {}},
    "카테고리": {"select": {}},
    "본문": {"rich_text": {}},
    "요약": {"rich_text": {}},
    "원본URL": {"url": {}},
    "게시일": {"date": {}},
    "수집일": {"date": {}},
    "키워드": {"multi_select": {}},
    "콘텐츠해시": {"rich_text": {}},
    "상태": {"select": {}},
    "검색용텍스트": {"rich_text": {}},
    "첨부파일": {"rich_text": {}},
    "본문길이": {"number": {"format": "number"}},
    "table_headers": {"rich_text": {}},
    "table_rows": {"rich_text": {}},
    "normalized_items": {"rich_text": {}},
    "응답가이드": {"rich_text": {}},
}

STATS_SCHEMA: dict[str, dict[str, Any]] = {
    "session_id": {"rich_text": {}},
    "request_id": {"rich_text": {}},
    "llm_type": {"select": {}},
    "allow_llm": {"checkbox": {}},
    "requires_llm_confirmation": {"checkbox": {}},
    "질문일시": {"date": {}},
    "추출키워드": {"multi_select": {}},
    "검색결과유무": {"checkbox": {}},
    "응답방식": {"select": {}},
    "답변내용": {"rich_text": {}},
    "참조URL": {"rich_text": {}},
    "응답시간": {"number": {"format": "number"}},
    "검색점수": {"number": {"format": "number"}},
    "실패사유": {"rich_text": {}},
    "응답유형": {"select": {}},
    "응답요약": {"rich_text": {}},
    "표시항목수": {"number": {"format": "number"}},
}


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


def rich_text(text: str, limit: int = 19000) -> list[dict[str, Any]]:
    value = (text or "").strip()[:limit]
    return [
        {"type": "text", "text": {"content": value[index : index + 1900]}}
        for index in range(0, len(value), 1900)
    ]


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
        for attempt in range(1, 4):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    json=payload,
                    timeout=15,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("[Notion 요청 재시도] method=%s path=%s attempt=%d error=%s", method, path, attempt, exc)
                time.sleep(min(3, attempt))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = response.text[:1000]
                retry_after_header = response.headers.get("retry-after")
                try:
                    retry_after = float(retry_after_header) if retry_after_header else float(attempt)
                except ValueError:
                    retry_after = float(attempt)
                logger.warning(
                    "[Notion API 재시도] method=%s path=%s status=%s attempt=%d retry_after=%.1f",
                    method,
                    path,
                    response.status_code,
                    attempt,
                    retry_after,
                )
                time.sleep(max(1.0, min(3.0, retry_after)))
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

    def ensure_database_schema(
        self,
        database_id: str,
        *,
        title_name: str,
        required: dict[str, dict[str, Any]],
        label: str,
    ) -> dict[str, Any]:
        database = self.validate_database(database_id, label)
        properties = database.get("properties") or {}
        title_property = next(
            (
                name
                for name, prop in properties.items()
                if isinstance(prop, dict) and prop.get("type") == "title"
            ),
            "",
        )
        renamed = False
        if title_property and title_property != title_name:
            self.request(
                "PATCH",
                f"/databases/{normalize_id(database_id)}",
                {"properties": {title_property: {"name": title_name}}},
            )
            renamed = True
            database = self.database(database_id)
            properties = database.get("properties") or {}

        missing = {name: schema for name, schema in required.items() if name not in properties}
        final_property_count = len(set(properties) | set(missing))
        if missing:
            self.request(
                "PATCH",
                f"/databases/{normalize_id(database_id)}",
                {"properties": missing},
            )
        return {
            "database_id": normalize_id(database_id),
            "label": label,
            "title_property": title_name,
            "renamed_title": renamed,
            "created_properties": list(missing),
            "property_count": final_property_count,
        }

    def ensure_knowledge_schema(self) -> dict[str, Any]:
        return self.ensure_database_schema(
            config.NOTION_KNOWLEDGE_DB_ID,
            title_name="제목",
            required=KNOWLEDGE_SCHEMA,
            label="크롤링 지식 DB",
        )

    def ensure_stats_schema(self) -> dict[str, Any]:
        return self.ensure_database_schema(
            config.NOTION_STATS_DB_ID,
            title_name="사용자질문",
            required=STATS_SCHEMA,
            label="챗봇 통계 DB",
        )

    def ensure_all_schemas(self) -> dict[str, Any]:
        return {
            "knowledge": self.ensure_knowledge_schema(),
            "stats": self.ensure_stats_schema(),
        }

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
            "출처구분": {
                "select": {
                    "name": "공식" if doc.source_type == "official" else "비공식 커뮤니티"
                }
            },
            "출처명": {"rich_text": rich_text(doc.source_label, 300)},
            "문서유형": {"select": {"name": doc.document_type}},
            "카테고리": {"select": {"name": (doc.category or "기타")[:100]}},
            "본문": {"rich_text": rich_text(doc.body)},
            "요약": {"rich_text": rich_text(doc.summary)},
            "원본URL": {"url": doc.source_url},
            "수집일": {"date": {"start": doc.collected_at}},
            "키워드": {"multi_select": [{"name": word[:100]} for word in doc.keywords[:15]]},
            "콘텐츠해시": {"rich_text": rich_text(doc.content_hash, 100)},
            "상태": {"select": {"name": status}},
            "검색용텍스트": {"rich_text": rich_text(doc.search_text)},
            "첨부파일": {"rich_text": rich_text("\n".join(doc.attachments))},
            "본문길이": {"number": len(doc.body)},
            "table_headers": {"rich_text": rich_text(json.dumps(doc.table_headers, ensure_ascii=False))},
            "table_rows": {"rich_text": rich_text(json.dumps(doc.table_rows, ensure_ascii=False))},
            "normalized_items": {"rich_text": rich_text(json.dumps(doc.normalized_items, ensure_ascii=False))},
            "응답가이드": {
                "rich_text": rich_text(
                    "원문 전체 출력 금지 · 학생용 핵심 요약 · 최대 3개 우선 표시 · "
                    + (
                        "공식 링크 제공"
                        if doc.source_type == "official"
                        else "비공식 참고자료로 명시 · 공식 사실 근거로 사용 금지"
                    )
                )
            },
        }
        if doc.published_at:
            props["게시일"] = {"date": {"start": doc.published_at}}
        return props

    @staticmethod
    def _body_blocks(doc: CrawlDocument) -> list[dict[str, Any]]:
        paragraphs = [part.strip() for part in re.split(r"\n{2,}|\n", doc.body) if part.strip()]
        blocks: list[dict[str, Any]] = [
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "🧭"},
                    "rich_text": rich_text(
                        f"출처: {doc.source_label} "
                        f"({'공식' if doc.source_type == 'official' else '비공식'}) | "
                        f"문서유형: {doc.document_type} | 카테고리: {doc.category} | "
                        f"게시일: {doc.published_at or '미확인'} | 수집일: {doc.collected_at[:10]}"
                    ),
                },
            },
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": rich_text("본문")},
            },
        ]
        for paragraph in paragraphs:
            chunks = [paragraph[i : i + 1900] for i in range(0, len(paragraph), 1900)]
            blocks.extend(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich_text(chunk)},
                }
                for chunk in chunks
            )
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
        blocks.append(
            {
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": doc.source_url},
            }
        )
        return blocks[:95]

    def replace_page_blocks(self, page_id: str, doc: CrawlDocument) -> None:
        cursor = None
        while True:
            query = "?page_size=100"
            if cursor:
                query += f"&start_cursor={cursor}"
            data = self.request("GET", f"/blocks/{page_id}/children{query}")
            for block in data.get("results") or []:
                if block.get("id"):
                    self.request("DELETE", f"/blocks/{block['id']}")
            if not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        self.request("PATCH", f"/blocks/{page_id}/children", {"children": self._body_blocks(doc)})

    def upsert_document(self, doc: CrawlDocument, database_id: str = config.NOTION_KNOWLEDGE_DB_ID) -> str:
        existing = self.find_by_url(database_id, doc.source_url)
        if existing:
            old_hash = self._property_text((existing.get("properties") or {}).get("콘텐츠해시", {}))
            if old_hash == doc.content_hash:
                # 제목·본문·게시일·첨부파일이 모두 같으면 Notion 페이지를 전혀 수정하지 않는다.
                # 수집일이나 상태만 갱신하는 PATCH도 하지 않아 진정한 증분 동기화를 보장한다.
                return "유지"
            self.request(
                "PATCH",
                f"/pages/{existing['id']}",
                {"properties": self._properties(doc, "변경")},
            )
            self.replace_page_blocks(existing["id"], doc)
            return "변경"
        payload = {
            "parent": {"database_id": normalize_id(database_id)},
            "properties": self._properties(doc, "신규"),
            "children": self._body_blocks(doc),
        }
        self.request("POST", "/pages", payload)
        return "신규"

    def upsert_many(self, documents: Iterable[CrawlDocument], progress_callback: Any | None = None) -> dict[str, int]:
        counts = {"신규": 0, "변경": 0, "유지": 0, "실패": 0}
        document_list = list(documents)
        total = len(document_list)
        logger.info("[Notion 저장 시작] total=%d", total)
        for idx, doc in enumerate(document_list, start=1):
            title = doc.title or doc.source_url
            logger.info("[Notion 저장중] %d/%d - %s", idx, total, title)
            if progress_callback:
                progress_callback(
                    {
                        "phase": "saving",
                        "index": idx,
                        "total": total,
                        "title": title,
                        "url": doc.source_url,
                        "counts": counts.copy(),
                    }
                )
            try:
                status = self.upsert_document(doc)
                counts[status] += 1
                logger.info("[Notion 저장완료] %d/%d - %s status=%s", idx, total, title, status)
            except Exception as exc:
                counts["실패"] += 1
                logger.exception("[Notion 저장실패] %s url=%s error=%s", title, doc.source_url, exc)
            finally:
                if progress_callback:
                    progress_callback(
                        {
                            "phase": "saved",
                            "index": idx,
                            "total": total,
                            "title": title,
                            "url": doc.source_url,
                            "counts": counts.copy(),
                        }
                    )
        logger.info("[Notion 전체 저장 완료] total=%d counts=%s", total, counts)
        return counts

    def upsert_curated_knowledge(self) -> dict[str, int]:
        """관리자 검증 지식을 일반 크롤링 문서와 동일한 DB에 동기화한다."""
        return self.upsert_many(curated_documents())

    def knowledge_documents(self, limit: int | None = None) -> list[dict[str, Any]]:
        pages = self.query_all(config.NOTION_KNOWLEDGE_DB_ID, limit=limit)
        documents = []
        for page in pages:
            props = page.get("properties") or {}
            get = lambda name: self._property_text(props.get(name, {}))
            keywords_prop = props.get("키워드", {})
            keywords = [x.get("name", "") for x in keywords_prop.get("multi_select", [])]
            normalized_items = self._json_property(get("normalized_items"), [])
            course_names = list(
                dict.fromkeys(
                    item.get("course_name") or item.get("title")
                    for item in normalized_items
                    if item.get("course_name") or item.get("title")
                )
            )
            source_url = get("원본URL") or config.CRAWL_START_URL
            documents.append(
                {
                    "page_id": page.get("id", ""),
                    "title": get("제목"),
                    "category": get("카테고리"),
                    "document_type": get("문서유형"),
                    "body": get("본문"),
                    "summary": get("요약"),
                    "source_url": source_url,
                    "published_at": get("게시일"),
                    "collected_at": get("수집일"),
                    "keywords": keywords,
                    "content_hash": get("콘텐츠해시"),
                    "status": get("상태"),
                    "search_text": get("검색용텍스트"),
                    "table_headers": self._json_property(get("table_headers"), []),
                    "table_rows": self._json_property(get("table_rows"), []),
                    "normalized_items": normalized_items,
                    "course_names": course_names,
                    "response_guide": get("응답가이드"),
                    "source_type": (
                        "community"
                        if get("출처구분") == "비공식 커뮤니티"
                        else "official"
                    ),
                    "source_label": (
                        get("출처명")
                        or "한국방송통신대학교 컴퓨터과학과 공식 홈페이지"
                    ),
                }
            )
        return documents

    @staticmethod
    def _json_property(value: str, default: Any) -> Any:
        try:
            return json.loads(value) if value else default
        except (TypeError, json.JSONDecodeError):
            return default

    def recent_knowledge(self, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_knowledge_schema()
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
                    "source_type": (
                        "community"
                        if get("출처구분") == "비공식 커뮤니티"
                        else "official"
                    ),
                    "source_label": get("출처명"),
                }
            )
        return documents
