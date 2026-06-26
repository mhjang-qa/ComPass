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
from crawler import DATA_TIERS, classify_data_tier, search_recency_boost, should_collect_document_record
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
SCHEDULE_URL = config.SCHEDULE_URL
NOTICE_URL = config.NOTICE_URL
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
SCHEDULE_ALLOWED_CATEGORIES = ("학과일정", "학사일정", "공지사항")
SCHEDULE_BAD_RE = re.compile(r"벼룩시장|학생광장|중고장터|자유게시판|market|student", re.IGNORECASE)
SCHEDULE_KEYWORD_RE = re.compile(r"일정|학사|수강신청|기말|중간|형성평가|시험|평가|등록|휴학|복학|마감|신청", re.IGNORECASE)
SCHEDULE_DETAIL_RE = re.compile(r"^https://cs\.knou\.ac\.kr/bbs/cs1/.+/artclView\.do", re.IGNORECASE)
NOTICE_BAD_RE = re.compile(r"교육과정|교과목|교수진|학과일정|벼룩시장|학생광장|중고장터|learningInformation", re.IGNORECASE)
NOTICE_URL_RE = re.compile(r"^https://cs\.knou\.ac\.kr/(?:bbs/cs1/.+/artclView\.do|cs1/4812/subview\.do)", re.IGNORECASE)
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


def validate_schedule_document(doc: dict[str, Any]) -> bool:
    title = (doc.get("title") or "").strip()
    category = doc.get("category") or ""
    document_type = doc.get("document_type") or ""
    source_url = doc.get("source_url") or ""
    text = f"{title} {category} {document_type} {doc.get('summary') or ''} {doc.get('body') or ''}"
    if not title or re.fullmatch(r"\d+", title):
        return False
    if SCHEDULE_BAD_RE.search(f"{source_url} {category} {title}"):
        return False
    allowed_url = source_url == SCHEDULE_URL or bool(SCHEDULE_DETAIL_RE.search(source_url))
    if not allowed_url:
        return False
    if source_url == SCHEDULE_URL:
        return True
    if document_type in {"schedule", "학과일정"} and category == "학과일정":
        return True
    if category in SCHEDULE_ALLOWED_CATEGORIES and (source_url == NOTICE_URL or SCHEDULE_DETAIL_RE.search(source_url)) and SCHEDULE_KEYWORD_RE.search(text):
        return True
    return False


def validate_notice_document(doc: dict[str, Any]) -> bool:
    title = (doc.get("title") or "").strip()
    category = doc.get("category") or ""
    document_type = doc.get("document_type") or ""
    source_url = doc.get("source_url") or ""
    marker = f"{source_url} {category} {document_type} {title}"
    if not title or re.fullmatch(r"\d+", title):
        return False
    if NOTICE_BAD_RE.search(marker):
        return False
    if source_url == NOTICE_URL:
        return True
    if not NOTICE_URL_RE.search(source_url):
        return False
    return (
        "공지" in category
        or document_type in {"공지사항", "게시물"}
        or "공지" in title
    )


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
            if not payload.get("faculty_catalog"):
                payload["faculty_catalog"] = self._build_faculty_catalog(
                    payload.get("documents") or []
                )
            with self._lock:
                self.payload = payload
        except (OSError, json.JSONDecodeError):
            with self._lock:
                self.payload = {"built_at": None, "documents": [], "course_catalog": [], "faculty_catalog": []}

    def rebuild(self, documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        documents = documents if documents is not None else NotionClient().knowledge_documents()
        indexed = []
        excluded = 0
        tier_counts = {tier: 0 for tier in DATA_TIERS}
        excluded_by_tier = {tier: 0 for tier in DATA_TIERS}
        for doc in documents:
            doc = {
                **doc,
                "source_type": doc.get("source_type") or "official",
                "source_label": doc.get("source_label") or "",
            }
            tier = classify_data_tier(doc)
            doc.setdefault("data_tier", tier["data_tier"])
            doc["data_tier"] = doc.get("data_tier") or tier["data_tier"]
            doc["active"] = tier["active"] if doc.get("active") is None else bool(doc.get("active"))
            doc["archive_reason"] = doc.get("archive_reason") or tier["archive_reason"]
            doc["freshness_score"] = doc.get("freshness_score") or tier["freshness_score"]
            status = (doc.get("status") or "").lower()
            searchable = (
                doc["data_tier"] in {"CORE", "ACTIVE_NOTICE", "IMPORTANT_ARCHIVE"}
                or (doc["data_tier"] == "TEMPORARY" and doc["active"])
            )
            if status in {"archived", "noise"} or not doc["active"] or doc["data_tier"] == "NOISE" or not searchable or not should_collect_document_record(doc)[0]:
                excluded += 1
                if doc["data_tier"] in excluded_by_tier:
                    excluded_by_tier[doc["data_tier"]] += 1
                continue
            if doc["data_tier"] in tier_counts:
                tier_counts[doc["data_tier"]] += 1
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
        faculty_catalog = self._build_faculty_catalog(indexed)
        new_payload = {
            "built_at": datetime.now().astimezone().isoformat(),
            "documents": indexed,
            "course_catalog": course_catalog,
            "faculty_catalog": faculty_catalog,
            "tier_counts": tier_counts,
            "excluded_by_tier": excluded_by_tier,
            "excluded": excluded,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(new_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            self.payload = new_payload
        return {
            "built_at": new_payload["built_at"],
            "documents": len(indexed),
            "excluded": excluded,
            "tier_counts": tier_counts,
            "excluded_by_tier": excluded_by_tier,
            "courses": len(course_catalog),
            "faculty": len(faculty_catalog),
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

    @staticmethod
    def _build_faculty_catalog(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for doc in documents:
            source_url = doc.get("source_url") or ""
            marker = f"{doc.get('title') or ''} {doc.get('category') or ''} {doc.get('document_type') or ''}"
            if source_url != FACULTY_URL and "교수진" not in marker:
                continue
            for item in doc.get("normalized_items") or []:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                subjects = item.get("subjects") or [
                    *(item.get("subjects_undergraduate") or []),
                    *(item.get("subjects_graduate") or []),
                ]
                catalog[name] = {
                    "name": name,
                    "position": item.get("position") or item.get("title") or "교수",
                    "title": item.get("title") or item.get("position") or "교수",
                    "email": item.get("email") or "",
                    "phone": item.get("phone") or "",
                    "subjects": subjects,
                    "subjects_undergraduate": item.get("subjects_undergraduate") or subjects,
                    "subjects_graduate": item.get("subjects_graduate") or [],
                    "research": item.get("research") or [],
                    "homepage_url": item.get("homepage_url") or "",
                    "source_url": source_url or FACULTY_URL,
                    "fallback_url": FACULTY_URL,
                    "link_label": "교수진 페이지 바로가기",
                }
        return sorted(catalog.values(), key=lambda item: item["name"])

    def faculty_catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.payload.get("faculty_catalog") or [])

    def detect_faculty(self, query: str) -> dict[str, Any] | None:
        compact = re.sub(r"\s+", "", query or "")
        compact_without_title = re.sub(r"(교수님|교수|선생님)", "", compact)
        matches = []
        for item in self.faculty_catalog():
            name = re.sub(r"\s+", "", item.get("name") or "")
            if name and (name in compact or name in compact_without_title):
                matches.append((len(name), item))
        return max(matches, key=lambda match: match[0])[1] if matches else None

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
        allowed_source_urls = set(filters.get("source_urls") or [])
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
            and (not allowed_source_urls or (doc.get("source_url") or "") in allowed_source_urls)
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
            if quick_intent == "schedule":
                if not validate_schedule_document(doc):
                    score -= 200
                if doc.get("document_type") in {"schedule", "학과일정"}:
                    score += 100
                if (doc.get("category") or "") == "학과일정":
                    score += 80
                if (doc.get("category") or "") == "공지사항" and SCHEDULE_KEYWORD_RE.search(text):
                    score += 50
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
            score += self._tier_boost(doc) + search_recency_boost(doc)
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
            official = [hit for hit in ranked if validate_schedule_document(hit)]
            if official:
                return official[:top_k]
            return []
        if quick_intent == "notice":
            notices = [hit for hit in ranked if validate_notice_document(hit)]
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
            faculty = len(self.payload.get("faculty_catalog") or [])
        return {
            "built_at": built_at,
            "documents": documents,
            "courses": courses,
            "faculty": faculty,
            "tier_counts": dict(self.payload.get("tier_counts") or {}),
            "excluded_by_tier": dict(self.payload.get("excluded_by_tier") or {}),
            "excluded": self.payload.get("excluded", 0),
            "path": str(self.path),
        }

    @staticmethod
    def _tier_boost(doc: dict[str, Any]) -> float:
        return {
            "CORE": 40.0,
            "IMPORTANT_ARCHIVE": 20.0,
            "ACTIVE_NOTICE": 10.0,
            "TEMPORARY": 5.0,
            "NOISE": -100.0,
        }.get(doc.get("data_tier") or "", 0.0)
