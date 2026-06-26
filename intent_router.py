"""ComPass 자연어 의도 라우터.

검색 전에 질문을 정규화하고 교수진/과목/일정 등 의도를 먼저 결정한다.
일반 RAG 검색은 이 라우터에서 명확한 의도를 찾지 못한 경우에만 사용한다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


DICTIONARY_PATH = Path(__file__).resolve().parent / "data" / "intent_dictionary.json"


@lru_cache(maxsize=1)
def load_dictionary() -> dict[str, Any]:
    try:
        return json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"synonyms": {}, "professors": [], "professor_keywords": [], "intent_keywords": {}}


def normalize_question(question: str) -> str:
    """띄어쓰기·유사어 차이를 줄여 의도 분석 입력으로 사용한다."""
    text = re.sub(r"\s+", " ", question or "").strip()
    dictionary = load_dictionary()
    for source, target in sorted((dictionary.get("synonyms") or {}).items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(re.escape(source), target, text, flags=re.IGNORECASE)
    return text


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _professor_names(faculty_catalog: list[dict[str, Any]] | None = None) -> list[str]:
    dictionary = load_dictionary()
    names = {name for name in dictionary.get("professors", []) if name}
    for item in faculty_catalog or []:
        name = (item.get("name") or "").strip()
        if name:
            names.add(name)
    return sorted(names, key=len, reverse=True)


def _match_professor_name(question: str, faculty_catalog: list[dict[str, Any]] | None = None) -> str:
    compact = compact_text(question)
    for name in _professor_names(faculty_catalog):
        if compact_text(name) in compact:
            return name
    return ""


def _contains_any(question: str, values: list[str]) -> bool:
    compact = compact_text(question)
    return any(compact_text(value) in compact for value in values)


def detect_intent(question: str, faculty_catalog: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """질문을 intent/entity/search_scope로 분류한다.

    반환 예:
    {"intent": "professor_detail", "confidence": 0.99, "entity": {"name": "손진곤"}, "search_scope": ["professor"]}
    """
    normalized = normalize_question(question)
    dictionary = load_dictionary()
    intent_keywords = dictionary.get("intent_keywords") or {}

    professor_name = _match_professor_name(normalized, faculty_catalog)
    if professor_name:
        return {
            "intent": "professor_detail",
            "confidence": 0.99,
            "entity": {"name": professor_name},
            "normalized_question": normalized,
            "search_scope": ["professor"],
        }

    professor_keywords = dictionary.get("professor_keywords") or []
    if _contains_any(normalized, professor_keywords) or re.fullmatch(r"교수(?:진)?", compact_text(normalized) or ""):
        return {
            "intent": "professor_list",
            "confidence": 0.97,
            "entity": {},
            "normalized_question": normalized,
            "search_scope": ["professor"],
        }

    course_grade_re = re.compile(r"[ABC]\s*(?:이상|받|맞)|성적\s*잘|점수\s*잘|잘하려면|맞으려면|받으려면|공부법|시험\s*대비|학습\s*전략", re.IGNORECASE)
    if course_grade_re.search(normalized):
        return {"intent": "course_grade", "confidence": 0.9, "entity": {}, "normalized_question": normalized, "search_scope": ["course"]}

    course_difficulty_re = re.compile(r"난이도|어렵|쉬운|공부량|수업\s*부담|학습\s*부담", re.IGNORECASE)
    if course_difficulty_re.search(normalized):
        return {"intent": "course_difficulty", "confidence": 0.86, "entity": {}, "normalized_question": normalized, "search_scope": ["course"]}

    course_order_re = re.compile(r"선수\s*지식|선수\s*과목|먼저|수강\s*순서|학습\s*순서", re.IGNORECASE)
    if course_order_re.search(normalized):
        return {"intent": "course_order", "confidence": 0.84, "entity": {}, "normalized_question": normalized, "search_scope": ["course"]}

    if _contains_any(normalized, intent_keywords.get("curriculum", [])):
        return {"intent": "curriculum", "confidence": 0.9, "entity": {}, "normalized_question": normalized, "search_scope": ["curriculum"]}
    if _contains_any(normalized, intent_keywords.get("schedule", [])):
        return {"intent": "schedule", "confidence": 0.9, "entity": {}, "normalized_question": normalized, "search_scope": ["schedule"]}
    notice_re = re.compile(r"최근\s*공지|공지\s*사항|학과\s*공지|(?<!인)공지(?!능)", re.IGNORECASE)
    if notice_re.search(normalized):
        return {"intent": "notice", "confidence": 0.88, "entity": {}, "normalized_question": normalized, "search_scope": ["notice"]}
    if _contains_any(normalized, intent_keywords.get("graduation", [])):
        return {"intent": "graduation", "confidence": 0.84, "entity": {}, "normalized_question": normalized, "search_scope": ["graduation"]}
    if _contains_any(normalized, intent_keywords.get("faq", [])):
        return {"intent": "faq", "confidence": 0.84, "entity": {}, "normalized_question": normalized, "search_scope": ["faq"]}
    if _contains_any(normalized, intent_keywords.get("contact", [])):
        return {"intent": "contact", "confidence": 0.82, "entity": {}, "normalized_question": normalized, "search_scope": ["contact"]}

    course_info_re = re.compile(r"무슨\s*과목|어떤\s*과목|과목\s*소개|뭐\s*배우|수업\s*내용", re.IGNORECASE)
    if course_info_re.search(normalized):
        return {"intent": "course_info", "confidence": 0.82, "entity": {}, "normalized_question": normalized, "search_scope": ["course"]}

    return {
        "intent": "general_search",
        "confidence": 0.3,
        "entity": {},
        "normalized_question": normalized,
        "search_scope": ["general"],
    }
