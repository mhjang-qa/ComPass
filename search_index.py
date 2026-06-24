"""Notion 지식 문서 기반 로컬 검색 인덱스."""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import config
from notion_client import NotionClient

SYNONYMS = {
    "등록금": ["수업료", "납부", "학비"],
    "교수": ["교수진", "선생님"],
    "과목": ["교과목", "수업", "강의"],
    "교육과정": ["교과과정", "커리큘럼"],
    "일정": ["학사일정", "학과일정", "스케줄"],
    "공지": ["공지사항", "알림"],
    "시험": ["중간고사", "기말고사", "평가"],
    "과제": ["레포트", "보고서", "출석수업대체"],
    "졸업": ["졸업요건", "학위"],
    "자격증": ["정보처리기사", "sqld", "자격"],
    "데이터베이스": ["db", "데이터베이스시스템"],
    "FAQ": ["자주하는질문", "자주 묻는 질문"],
}
FACULTY_URL = "https://cs.knou.ac.kr/cs1/4786/subview.do"
CURRICULUM_URL = "https://cs.knou.ac.kr/cs1/4789/subview.do"
SCHEDULE_URL = "https://cs.knou.ac.kr/cs1/4792/subview.do"
NOTICE_URL = "https://cs.knou.ac.kr/cs1/4812/subview.do"
COURSE_GUIDE_URL = "https://cs.knou.ac.kr/cs1/4791/subview.do"
COURSE_DOCUMENT_TYPES = {"과목상세", "교과목목록", "교육과정표", "검증지식"}
COURSE_ALIASES = {
    "인공지능": ["AI", "에이아이"],
    "파이썬프로그래밍기초": ["파이썬", "파이썬기초", "Python"],
    "데이터베이스시스템": ["데이터베이스", "DB"],
    "컴퓨터구조": ["컴구"],
    "운영체제": ["OS"],
    "정보통신망": ["네트워크"],
    "소프트웨어공학": ["소공"],
}
FACULTY_QUERY_RE = re.compile(r"교수진|교수\s*(정보|소개|목록)?|선생님|담당\s*교수", re.IGNORECASE)
QUICK_INTENTS = (
    ("curriculum", re.compile(r"교육과정|교과과정|커리큘럼", re.IGNORECASE), ("교육과정", "교과과정", "교과정보")),
    ("notice", re.compile(r"최근\s*공지|공지사항|학과\s*공지", re.IGNORECASE), ("공지사항", "공지", "학과광장")),
    ("schedule", re.compile(r"학과\s*일정|학사\s*일정|일정", re.IGNORECASE), ("학과일정", "학사일정")),
)
STOPWORDS = {
    "무엇", "뭐", "어떻게", "알려줘", "알려주세요", "대한", "관련", "있는", "있나요",
    "인가요", "합니다", "해주세요", "그리고", "에서", "으로", "컴퓨터과학과",
}


def tokenize(text: str) -> list[str]:
    raw = re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9+.#_-]{1,}", (text or "").lower())
    tokens = [token for token in raw if token not in STOPWORDS and len(token) >= 2]
    expanded = list(tokens)
    compact = re.sub(r"\s+", "", (text or "").lower())
    for key, values in SYNONYMS.items():
        group = [key.lower(), *(value.lower() for value in values)]
        if any(term in compact for term in group):
            expanded.extend(group)
    return list(dict.fromkeys(expanded))


class SearchIndex:
    def __init__(self, path: Path = config.INDEX_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.payload: dict[str, Any] = {"built_at": None, "documents": [], "course_catalog": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not payload.get("course_catalog"):
                payload["course_catalog"] = self._build_course_catalog(
                    payload.get("documents") or []
                )
            with self._lock:
                self.payload = payload
        except (OSError, json.JSONDecodeError):
            with self._lock:
                self.payload = {"built_at": None, "documents": [], "course_catalog": []}

    def rebuild(self, documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        documents = documents if documents is not None else NotionClient().knowledge_documents()
        indexed = []
        for doc in documents:
            doc = {
                **doc,
                "source_type": doc.get("source_type") or "official",
                "source_label": doc.get("source_label") or "",
            }
            text = doc.get("search_text") or " ".join(
                [
                    doc.get("title", ""),
                    doc.get("document_type", ""),
                    doc.get("category", ""),
                    doc.get("summary", ""),
                    " ".join(doc.get("keywords") or []),
                    self._normalized_item_text(doc.get("normalized_items") or []),
                    doc.get("body", ""),
                ]
            )
            item = {**doc, "tokens": tokenize(text), "search_text": text}
            indexed.append(item)
        course_catalog = self._build_course_catalog(indexed)
        new_payload = {
            "built_at": datetime.now().astimezone().isoformat(),
            "documents": indexed,
            "course_catalog": course_catalog,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(new_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            self.payload = new_payload
        return {
            "built_at": new_payload["built_at"],
            "documents": len(indexed),
            "courses": len(course_catalog),
        }

    @staticmethod
    def _normalized_item_text(items: list[dict[str, Any]]) -> str:
        """교수진·과목 구조화 필드도 키워드 검색 대상에 포함한다."""
        values: list[str] = []
        for item in items:
            for key in (
                "name",
                "position",
                "title",
                "email",
                "phone",
                "course_name",
                "category",
                "overview",
                "homepage_url",
            ):
                value = item.get(key)
                if value:
                    values.append(str(value))
            for key in ("subjects", "subjects_undergraduate", "subjects_graduate", "research", "topics"):
                value = item.get(key)
                if isinstance(value, list):
                    values.extend(str(part) for part in value if part)
        return " ".join(values)

    @staticmethod
    def _build_course_catalog(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for doc in documents:
            document_type = doc.get("document_type") or ""
            if document_type not in COURSE_DOCUMENT_TYPES:
                continue
            items = doc.get("normalized_items") or []
            if document_type == "과목상세" and not items:
                items = [{"course_name": doc.get("title") or ""}]
            for item in items:
                name = (item.get("course_name") or item.get("title") or "").strip()
                if not name:
                    continue
                aliases = [name, re.sub(r"\s+", "", name), *COURSE_ALIASES.get(name, [])]
                entry = catalog.setdefault(
                    name,
                    {
                        "course_name": name,
                        "course_code": item.get("course_code") or "",
                        "grade": item.get("grade") or "",
                        "semester": item.get("semester") or "",
                        "category": item.get("category") or "",
                        "overview": item.get("overview") or "",
                        "topics": item.get("topics") or item.get("detail_topics") or [],
                        "detail_url": item.get("detail_url") or item.get("source_url") or "",
                        "fallback_url": item.get("fallback_url") or COURSE_GUIDE_URL,
                        "aliases": [],
                        "document_ids": [],
                        "source_url": doc.get("source_url") or COURSE_GUIDE_URL,
                        "document_types": [],
                    },
                )
                for field in ("course_code", "grade", "semester", "category", "overview", "detail_url", "fallback_url"):
                    if not entry.get(field) and item.get(field):
                        entry[field] = item.get(field)
                if not entry.get("topics") and (item.get("topics") or item.get("detail_topics")):
                    entry["topics"] = item.get("topics") or item.get("detail_topics")
                entry["aliases"] = list(dict.fromkeys([*entry["aliases"], *aliases]))
                if doc.get("page_id"):
                    entry["document_ids"] = list(
                        dict.fromkeys([*entry["document_ids"], doc["page_id"]])
                    )
                if document_type not in entry["document_types"]:
                    entry["document_types"].append(document_type)
                if document_type == "과목상세":
                    entry["source_url"] = doc.get("source_url") or entry["source_url"]
                    entry["detail_url"] = item.get("detail_url") or doc.get("source_url") or entry["detail_url"]
        return sorted(catalog.values(), key=lambda item: (-len(item["course_name"]), item["course_name"]))

    def course_catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.payload.get("course_catalog") or [])

    def detect_course(self, query: str) -> dict[str, Any] | None:
        compact = re.sub(r"\s+", "", query or "").lower()
        candidates = []
        for course in self.course_catalog():
            for alias in course.get("aliases") or []:
                normalized = re.sub(r"\s+", "", alias).lower()
                if normalized and normalized in compact:
                    candidates.append((len(normalized), course))
                    break
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def search(
        self,
        query: str,
        top_k: int = config.SEARCH_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        filters = filters or {}
        allowed_types = set(filters.get("document_types") or [])
        excluded_types = set(filters.get("exclude_document_types") or [])
        excluded_categories = [value.lower() for value in filters.get("exclude_categories") or []]
        allowed_source_types = set(filters.get("source_types") or [])
        course_name = (filters.get("course_name") or "").strip()

        def matches_course(doc: dict[str, Any]) -> bool:
            if not course_name:
                return True
            target = re.sub(r"\s+", "", course_name).lower()
            title = re.sub(r"\s+", "", doc.get("title") or "").lower()
            item_names = [
                re.sub(r"\s+", "", item.get("course_name") or item.get("title") or "").lower()
                for item in (doc.get("normalized_items") or [])
            ]
            return title == target or target in item_names

        with self._lock:
            payload_documents = list(self.payload.get("documents") or [])
        documents = [
            doc
            for doc in payload_documents
            if (not allowed_types or doc.get("document_type") in allowed_types)
            and (
                not allowed_source_types
                or (doc.get("source_type") or "official") in allowed_source_types
            )
            and doc.get("document_type") not in excluded_types
            and not any(term in (doc.get("category") or "").lower() for term in excluded_categories)
            and matches_course(doc)
        ]
        document_frequency = Counter()
        for doc in documents:
            document_frequency.update(set(doc.get("tokens") or []))
        total = max(len(documents), 1)
        hits = []
        compact_query = re.sub(r"\s+", "", query.lower())
        faculty_intent = bool(FACULTY_QUERY_RE.search(query))
        quick_intent, quick_intent_terms = next(
            ((name, terms) for name, pattern, terms in QUICK_INTENTS if pattern.search(query)),
            ("", ()),
        )

        for doc in documents:
            tokens = doc.get("tokens") or []
            token_counts = Counter(tokens)
            score = 0.0
            matched = []
            title = (doc.get("title") or "").lower()
            category = (doc.get("category") or "").lower()
            text = (doc.get("search_text") or "").lower()
            compact_text = re.sub(r"\s+", "", text)
            source_url = doc.get("source_url") or ""
            if course_name:
                normalized_course = re.sub(r"\s+", "", course_name).lower()
                normalized_title = re.sub(r"\s+", "", title)
                normalized_items = [
                    re.sub(r"\s+", "", item.get("course_name") or "").lower()
                    for item in (doc.get("normalized_items") or [])
                ]
                if normalized_course in normalized_items:
                    score += 100
                if normalized_title == normalized_course:
                    score += 80
                if doc.get("document_type") == "과목상세":
                    score += 80
                if "교과목" in category or "과목상세" in category:
                    score += 60
                if doc.get("document_type") in {"게시물", "게시판목록"}:
                    score -= 100
                if source_url == FACULTY_URL or "교수진" in category:
                    score -= 80
            if faculty_intent:
                if source_url == FACULTY_URL or "교수진" in title or "교수진" in category:
                    score += 100
                else:
                    score -= 25
            if quick_intent_terms:
                if any(term.lower() in f"{title} {category}" for term in quick_intent_terms):
                    score += 45
            for token in query_tokens:
                count = token_counts.get(token, 0)
                if count:
                    idf = math.log((total + 1) / (document_frequency[token] + 1)) + 1
                    score += (4 + min(count, 3)) * idf
                    matched.append(token)
                elif token in compact_text:
                    score += 3.0
                    matched.append(token)
                else:
                    best = max((SequenceMatcher(None, token, candidate).ratio() for candidate in tokens), default=0)
                    if best >= 0.82:
                        score += best * 2.5
            for token in query_tokens:
                if token in title:
                    score += 7
                if token in category:
                    score += 4
            if compact_query and compact_query in compact_text:
                score += 12
            coverage = len(set(matched)) / max(len(set(query_tokens)), 1)
            score += coverage * 10
            if score > 0:
                hits.append(
                    {
                        **{key: value for key, value in doc.items() if key != "tokens"},
                        "score": round(score, 2),
                        "matched_keywords": list(dict.fromkeys(matched)),
                    }
                )
        ranked = sorted(hits, key=lambda item: item["score"], reverse=True)
        if faculty_intent:
            official_faculty = [hit for hit in ranked if hit.get("source_url") == FACULTY_URL]
            if official_faculty:
                return official_faculty[:top_k]
            faculty_hits = [
                hit
                for hit in ranked
                if "교수진" in (hit.get("title") or "")
                or "교수진" in (hit.get("category") or "")
            ]
            if faculty_hits:
                return faculty_hits[:top_k]
        if quick_intent == "curriculum":
            official = [hit for hit in ranked if hit.get("source_url") == CURRICULUM_URL]
            if official:
                return official[:top_k]
            general_pages = [
                hit
                for hit in ranked
                if hit.get("document_type") == "일반페이지"
                and any(term in f"{hit.get('title', '')} {hit.get('category', '')}" for term in ("교과과정", "교육과정"))
            ]
            if general_pages:
                return general_pages[:top_k]
        if quick_intent == "schedule":
            official = [hit for hit in ranked if hit.get("source_url") == SCHEDULE_URL]
            if official:
                return official[:top_k]
        if quick_intent == "notice":
            notices = [
                hit
                for hit in ranked
                if (
                    "공지사항" in (hit.get("category") or "")
                    or "공지" in (hit.get("category") or "")
                )
                and (
                    hit.get("document_type") == "게시물"
                    or "artclView.do" in (hit.get("source_url") or "")
                )
            ]
            if not notices:
                notices = [
                    hit
                    for hit in ranked
                    if "공지사항" in (hit.get("category") or "")
                    or "공지사항" == (hit.get("title") or "").strip()
                ]
            if notices:
                notices.sort(
                    key=lambda hit: (hit.get("published_at") or "", hit.get("score") or 0),
                    reverse=True,
                )
                return notices[:top_k]
        return ranked[:top_k]

    def status(self) -> dict[str, Any]:
        with self._lock:
            built_at = self.payload.get("built_at")
            documents = len(self.payload.get("documents") or [])
            courses = len(self.payload.get("course_catalog") or [])
        return {
            "built_at": built_at,
            "documents": documents,
            "courses": courses,
            "path": str(self.path),
        }
