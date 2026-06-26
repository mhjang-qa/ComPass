"""ComPass 자연어 의도 라우터.

RAG 검색 전에 질문을 정규화하고 Intent/Entity/Search Scope를 결정한다.
규칙 기반 confidence가 낮을 때만 상위 계층에서 LLM 보조 분류를 시도할 수 있다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


DICTIONARY_PATH = Path(__file__).resolve().parent / "data" / "intent_dictionary.json"
INTENT_PRIORITY = [
    "faculty_detail",
    "course_detail",
    "course_difficulty",
    "course_grade_strategy",
    "course_order",
    "course_roadmap",
    "faculty_list",
    "curriculum",
    "schedule",
    "notice",
    "graduation",
    "faq",
    "contact",
    "smalltalk",
    "general_search",
    "out_of_scope",
]
SEARCH_SCOPES = {
    "faculty_list": ["faculty"],
    "faculty_detail": ["faculty"],
    "curriculum": ["curriculum"],
    "course_detail": ["course_detail", "curriculum"],
    "course_difficulty": ["course_detail", "curriculum"],
    "course_grade_strategy": ["course_detail", "curriculum"],
    "course_order": ["course_detail", "curriculum"],
    "course_roadmap": ["curriculum", "course_detail"],
    "schedule": ["schedule"],
    "notice": ["notice"],
    "graduation": ["graduation", "curated_knowledge"],
    "faq": ["faq"],
    "contact": ["contact", "core"],
    "smalltalk": [],
    "general_search": ["general"],
    "out_of_scope": [],
}


@lru_cache(maxsize=1)
def load_dictionary() -> dict[str, Any]:
    try:
        return json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"synonyms": {}, "professors": [], "courses": [], "professor_keywords": [], "intent_keywords": {}}


def compact_text(value: str) -> str:
    return re.sub(r"[\s\?\!\.,~요은는이가을를에에서으로로해줘알려줘]", "", value or "").lower()


def normalize_question(question: str) -> str:
    """띄어쓰기·유사어·구두점을 정리한다."""
    text = re.sub(r"\s+", " ", question or "").strip()
    dictionary = load_dictionary()
    for source, target in sorted((dictionary.get("synonyms") or {}).items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(re.escape(source), target, text, flags=re.IGNORECASE)
    text = re.sub(r"[?!~]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _catalog_list(catalogs: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not catalogs:
        return []
    value = catalogs.get(key) or []
    return value if isinstance(value, list) else []


def _faculty_names(catalogs: dict[str, Any] | None = None) -> list[str]:
    dictionary = load_dictionary()
    names = {name for name in dictionary.get("professors", []) if name}
    for item in _catalog_list(catalogs, "faculty"):
        name = (item.get("name") or "").strip()
        if name:
            names.add(name)
    return sorted(names, key=len, reverse=True)


def _course_names(catalogs: dict[str, Any] | None = None) -> list[str]:
    dictionary = load_dictionary()
    names = {name for name in dictionary.get("courses", []) if name}
    for item in _catalog_list(catalogs, "courses"):
        for key in ("course_name", "title", "name"):
            name = (item.get(key) or "").strip()
            if name:
                names.add(name)
        for alias in item.get("aliases") or []:
            if alias:
                names.add(str(alias).strip())
    return sorted(names, key=len, reverse=True)


def _match_name(question: str, names: list[str]) -> str:
    compact = compact_text(question)
    for name in names:
        if compact_text(name) and compact_text(name) in compact:
            return name
    return ""


def _contains_any(question: str, values: list[str]) -> bool:
    compact = compact_text(question)
    return any(compact_text(value) in compact for value in values)


def extract_entities(question: str, catalogs: dict[str, Any] | None = None) -> dict[str, Any]:
    """교수명, 과목명, 학년/대상, 성적 목표를 추출한다."""
    normalized = normalize_question(question)
    entities: dict[str, Any] = {}
    faculty_name = _match_name(normalized, _faculty_names(catalogs))
    course_name = _match_name(normalized, _course_names(catalogs))
    if faculty_name:
        entities["faculty_name"] = faculty_name
        entities["name"] = faculty_name  # 기존 코드 호환
    if course_name:
        entities["course_name"] = course_name

    grade_match = re.search(r"([1-4])\s*학년", normalized)
    if grade_match:
        entities["grade"] = f"{grade_match.group(1)}학년"
    if re.search(r"편입생|편입", normalized):
        entities["target"] = "편입생"
    elif re.search(r"직장인", normalized):
        entities["target"] = "직장인"

    score_match = re.search(r"([ABC])\s*(?:이상|받|맞)", normalized, re.IGNORECASE)
    if score_match:
        entities["grade_goal"] = f"{score_match.group(1).upper()} 이상"
    return entities


def _result(intent: str, confidence: float, entities: dict[str, Any], normalized: str, reason: str = "") -> dict[str, Any]:
    return {
        "intent": intent,
        "confidence": confidence,
        "entities": entities,
        "entity": entities,  # 이전 구현 호환
        "normalized_question": normalized,
        "search_scope": SEARCH_SCOPES.get(intent, ["general"]),
        "answer_type": {
            "faculty_list": "faculty",
            "faculty_detail": "faculty_detail",
            "curriculum": "course_table",
            "notice": "notice_list",
            "schedule": "schedule_list",
        }.get(intent, intent),
        "reason": reason,
    }


def detect_intent(question: str, catalogs: dict[str, Any] | list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """질문을 Intent/Entity/Search Scope로 분류한다."""
    if isinstance(catalogs, list):
        catalogs = {"faculty": catalogs}
    normalized = normalize_question(question)
    compact = compact_text(normalized)
    dictionary = load_dictionary()
    entities = extract_entities(normalized, catalogs)

    if re.fullmatch(r"(안녕|안녕하세요|하이|hello|hi|고마워|감사합니다)", compact, re.IGNORECASE):
        return _result("smalltalk", 0.95, entities, normalized, "짧은 일상 대화")

    if entities.get("faculty_name"):
        return _result("faculty_detail", 0.99, entities, normalized, "교수명 직접 포함")

    course_name = entities.get("course_name")
    if course_name and re.search(r"무슨\s*과목|어떤\s*과목|뭐야|뭐\s*배우|설명|소개|어떤\s*과목|어떤\s*수업", normalized):
        return _result("course_detail", 0.94, entities, normalized, "과목 설명 질문")
    if course_name and re.search(r"난이도|어렵|힘든|공부량|들을만|수업\s*부담|학습\s*부담", normalized):
        return _result("course_difficulty", 0.92, entities, normalized, "과목 난이도 질문")
    if course_name and re.search(r"[ABC]\s*(?:이상|받|맞)|성적\s*잘|점수\s*잘|잘하려면|맞으려면|받으려면|공부법|시험\s*대비|학습\s*전략|어떻게\s*(?:공부|준비)", normalized, re.IGNORECASE):
        return _result("course_grade_strategy", 0.93, entities, normalized, "성적 목표/학습 전략 질문")
    if course_name and re.search(r"선수\s*지식|선수\s*과목|듣기\s*전|전에\s*뭐|먼저|수강\s*순서|학습\s*순서", normalized):
        return _result("course_order", 0.9, entities, normalized, "선수지식/수강순서 질문")

    if re.search(r"편입생|편입|직장인|처음|어떤\s*과목부터|과목\s*추천|수강\s*순서|로드맵|듣기\s*좋은|듣기\s*쉬운", normalized):
        return _result("course_roadmap", 0.88, entities, normalized, "수강 로드맵/추천 질문")

    professor_keywords = dictionary.get("professor_keywords") or []
    if _contains_any(normalized, professor_keywords) or re.fullmatch(r"교수(?:진)?", compact):
        return _result("faculty_list", 0.97, entities, normalized, "교수진 목록 질문")

    intent_keywords = dictionary.get("intent_keywords") or {}
    if _contains_any(normalized, intent_keywords.get("curriculum", [])):
        return _result("curriculum", 0.9, entities, normalized, "교육과정 질문")
    if _contains_any(normalized, intent_keywords.get("schedule", [])):
        return _result("schedule", 0.9, entities, normalized, "학과 일정 질문")
    if re.search(r"최근\s*공지(?:사항)?|새\s*공지(?:사항)?|학과\s*공지(?:사항)?|경진대회\s*공지(?:사항)?|시험\s*공지(?:사항)?|(?<!인)공지사항|(?<!인)공지(?!능)", normalized):
        return _result("notice", 0.88, entities, normalized, "공지사항 질문")
    if _contains_any(normalized, intent_keywords.get("graduation", [])):
        return _result("graduation", 0.86, entities, normalized, "졸업 요건 질문")
    if _contains_any(normalized, intent_keywords.get("faq", [])):
        return _result("faq", 0.84, entities, normalized, "FAQ 질문")
    if _contains_any(normalized, intent_keywords.get("contact", [])):
        return _result("contact", 0.84, entities, normalized, "연락처 질문")

    if course_name:
        return _result("course_detail", 0.78, entities, normalized, "과목명만 감지")

    return _result("general_search", 0.3, entities, normalized, "명확한 Intent 없음")
