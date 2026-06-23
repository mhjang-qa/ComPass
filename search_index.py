"""Notion 지식 문서 기반 로컬 검색 인덱스."""

from __future__ import annotations

import json
import math
import re
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
        self.payload: dict[str, Any] = {"built_at": None, "documents": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self.payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.payload = {"built_at": None, "documents": []}

    def rebuild(self, documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        documents = documents if documents is not None else NotionClient().knowledge_documents()
        indexed = []
        for doc in documents:
            text = doc.get("search_text") or " ".join(
                [
                    doc.get("title", ""),
                    doc.get("document_type", ""),
                    doc.get("category", ""),
                    doc.get("summary", ""),
                    " ".join(doc.get("keywords") or []),
                    doc.get("body", ""),
                ]
            )
            item = {**doc, "tokens": tokenize(text), "search_text": text}
            indexed.append(item)
        self.payload = {"built_at": datetime.now().astimezone().isoformat(), "documents": indexed}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"built_at": self.payload["built_at"], "documents": len(indexed)}

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
        excluded_categories = [value.lower() for value in filters.get("exclude_categories") or []]
        documents = [
            doc
            for doc in (self.payload.get("documents") or [])
            if (not allowed_types or doc.get("document_type") in allowed_types)
            and not any(term in (doc.get("category") or "").lower() for term in excluded_categories)
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
        return {
            "built_at": self.payload.get("built_at"),
            "documents": len(self.payload.get("documents") or []),
            "path": str(self.path),
        }
