from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from synonyms import apply_synonyms


DICTIONARY_PATH = Path(__file__).resolve().parent / "data" / "intent_dictionary.json"
INTENTS_PATH = Path(__file__).resolve().parent / "data" / "intents.json"
DEFAULT_COURSES = {
    "컴퓨터그래픽스",
    "컴퓨터그래픽",
    "데이터베이스시스템",
    "데이터베이스",
    "운영체제",
    "인공지능",
    "자료구조",
    "알고리즘",
    "파이썬프로그래밍기초",
}
INTENT_PRIORITY = [
    "faculty_detail",
    "campus_location",
    "course_detail",
    "course_difficulty",
    "course_study_tip",
    "course_grade_strategy",
    "course_order",
    "course_roadmap",
    "recent_notice",
    "faculty_list",
    "curriculum",
    "schedule",
    "transfer",
    "exam",
    "scholarship",
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
    "campus_location": ["university_common"],
    "recent_notice": ["notice"],
    "curriculum": ["curriculum"],
    "course_detail": ["course_detail", "curriculum"],
    "course_difficulty": ["course_detail", "curriculum"],
    "course_study_tip": ["course_detail", "curriculum"],
    "course_grade_strategy": ["course_detail", "curriculum"],
    "course_order": ["course_detail", "curriculum"],
    "course_roadmap": ["curriculum", "course_detail"],
    "schedule": ["schedule"],
    "notice": ["notice"],
    "transfer": ["transfer", "curated_knowledge", "curriculum"],
    "exam": ["exam", "notice", "schedule", "curated_knowledge"],
    "scholarship": ["scholarship", "notice", "curated_knowledge"],
    "graduation": ["graduation", "curated_knowledge"],
    "faq": ["faq"],
    "contact": ["contact", "core"],
    "smalltalk": [],
    "general_search": ["general"],
    "out_of_scope": [],
}


CAMPUS_LOCATION_RE = re.compile(
    r"지역\s*대학|지역대학|캠퍼스|지역\s*캠퍼스|지역캠퍼스|학습관|찾아가는\s*길|"
    r"방통대\s*위치|방송대\s*위치|지역대학\s*주소|지역\s*대학\s*주소",
    re.IGNORECASE,
)
CAMPUS_LOCATION_WITH_PLACE_RE = re.compile(
    r"(?:지역|대학|방통대|방송대|캠퍼스|학습관|컴퓨터과학과|학과).*(?:위치|주소)|"
    r"(?:위치|주소).*(?:지역|대학|방통대|방송대|캠퍼스|학습관|컴퓨터과학과|학과)",
    re.IGNORECASE,
)
GRADUATION_RE = re.compile(r"졸업|졸업\s*요건|졸업\s*학점|학위|graduation|degree", re.IGNORECASE)


def is_campus_location_query(question: str) -> bool:
    normalized = normalize_question(question)
    if GRADUATION_RE.search(normalized):
        return False
    return bool(CAMPUS_LOCATION_RE.search(normalized) or CAMPUS_LOCATION_WITH_PLACE_RE.search(normalized))


@lru_cache(maxsize=1)
def load_dictionary() -> dict[str, Any]:
    try:
        return json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"synonyms": {}, "professors": [], "courses": [], "professor_keywords": [], "intent_keywords": {}}


@lru_cache(maxsize=1)
def load_intents() -> dict[str, Any]:
    try:
        return json.loads(INTENTS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def compact_text(value: str) -> str:
    text = apply_synonyms(value or "")
    text = re.sub(
        r"(알려줘|알려주세요|보여줘|보여주세요|궁금해|있어|있나요|좀|해주세요|해줘|"
        r"컴퓨터과학과|한국방송통신대학교|방송대|학과에서)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"[\s\?\!\.,~요은는이가을를에에서으로로]", "", text).lower()


def normalize_query(question: str) -> str:
    # 질문 정리
    return compact_text(question)


def normalize_question(question: str) -> str:
    # 표기 흔들림 보정
    text = apply_synonyms(re.sub(r"\s+", " ", question or "").strip())
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
    names.update(DEFAULT_COURSES)
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
    # 엔티티 추출
    normalized = normalize_question(question)
    entities: dict[str, Any] = {}
    faculty_name = _match_name(normalized, _faculty_names(catalogs))
    course_name = _match_name(normalized, _course_names(catalogs))
    if faculty_name:
        entities["faculty_name"] = faculty_name
        entities["name"] = faculty_name  # 호환
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
        "entity": entities,  # 호환
        "normalized_question": normalized,
        "search_scope": SEARCH_SCOPES.get(intent, ["general"]),
        "answer_type": {
            "faculty_list": "faculty",
            "faculty_detail": "faculty_detail",
            "recent_notice": "notice_list",
            "curriculum": "course_table",
            "notice": "notice_list",
            "schedule": "schedule_list",
            "transfer": "course_roadmap",
            "exam": "text",
            "scholarship": "text",
        }.get(intent, intent),
        "reason": reason,
    }


def _match_configured_intent(normalized: str) -> tuple[str, float, str]:
    # intent 매칭
    compact = normalize_query(normalized)
    intents = load_intents()
    for intent in INTENT_PRIORITY:
        config = intents.get(intent) or {}
        keywords = config.get("keywords") or []
        normalized_keywords = [normalize_query(keyword) for keyword in keywords]
        target_compact = compact.replace("인공지능", "") if intent == "recent_notice" else compact
        if target_compact in normalized_keywords:
            return intent, 0.99, "intent exact match"
    for intent in INTENT_PRIORITY:
        config = intents.get(intent) or {}
        for keyword in config.get("keywords") or []:
            key = normalize_query(keyword)
            target_compact = compact.replace("인공지능", "") if intent == "recent_notice" else compact
            if key and (key in target_compact or target_compact in key):
                return intent, 0.9, "intent keyword/synonym match"
    return "", 0.0, ""


def detect_intent(question: str, catalogs: dict[str, Any] | list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # intent 분류
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

    if is_campus_location_query(normalized):
        return _result("campus_location", 0.93, entities, normalized, "지역대학/캠퍼스 위치 질문")

    course_name = entities.get("course_name")
    if course_name and re.search(r"난이도|어렵|힘든|공부량|들을만|수업\s*부담|학습\s*부담", normalized):
        return _result("course_difficulty", 0.92, entities, normalized, "과목 난이도 질문")
    if course_name and re.search(r"무슨\s*과목|어떤\s*과목|뭐야|뭐\s*배우|설명|소개|어떤\s*과목|어떤\s*수업", normalized):
        return _result("course_detail", 0.94, entities, normalized, "과목 설명 질문")
    if course_name and re.search(r"학점\s*잘|점수\s*잘|공부\s*어떻게|시험\s*준비|기말\s*준비|중간\s*준비|과제\s*준비|어떻게\s*(?:공부|준비)|잘하는\s*방법|A\+?\s*받|성적\s*잘", normalized, re.IGNORECASE):
        return _result("course_study_tip", 0.93, entities, normalized, "과목 공부법/성적 전략 질문")
    if course_name and re.search(r"[ABC]\s*(?:이상|받|맞)|성적\s*잘|점수\s*잘|잘하려면|맞으려면|받으려면|공부법|시험\s*대비|학습\s*전략|어떻게\s*(?:공부|준비)", normalized, re.IGNORECASE):
        return _result("course_grade_strategy", 0.93, entities, normalized, "성적 목표/학습 전략 질문")
    if course_name and re.search(r"선수\s*지식|선수\s*과목|듣기\s*전|전에\s*뭐|먼저|수강\s*순서|학습\s*순서", normalized):
        return _result("course_order", 0.9, entities, normalized, "선수지식/수강순서 질문")

    if re.search(r"편입생|편입|직장인|처음|어떤\s*과목부터|과목\s*추천|수강\s*순서|로드맵|듣기\s*좋은|듣기\s*쉬운", normalized):
        return _result("course_roadmap", 0.88, entities, normalized, "수강 로드맵/추천 질문")

    if re.search(r"경진\s*대회|공모전|총장배|소프트웨어\s*경진", normalized, re.IGNORECASE):
        return _result("notice", 0.9, entities, normalized, "경진대회/공모전 공지 질문")

    configured_intent, confidence, reason = _match_configured_intent(normalized)
    if configured_intent:
        if configured_intent == "faculty":
            configured_intent = "faculty_list"
        if configured_intent == "transfer":
            configured_intent = "course_roadmap"
        return _result(configured_intent, confidence, entities, normalized, reason)

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
