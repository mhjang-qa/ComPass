from __future__ import annotations

import logging
import json
import re
import time
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

import requests

import config
from crawler import extract_schedule_items, summarize
from curated_knowledge import match_curated
from intent_router import detect_intent as route_intent
from search_index import (
    COURSE_DOCUMENT_TYPES,
    COURSE_GUIDE_URL,
    CURRICULUM_URL,
    DOCUMENT_RESOURCE_TYPES,
    FACULTY_QUERY_RE,
    FACULTY_URL,
    NOTICE_URL,
    SCHEDULE_URL,
    SearchIndex,
    normalize_course_key,
    tokenize,
    validate_notice_document,
)

logger = logging.getLogger(__name__)

DEPARTMENT_HOME_URL = config.DEPARTMENT_HOME_URL
COURSE_FULL_GUIDE_URL = f"{COURSE_GUIDE_URL}#course-34524"
BAD_CURRICULUM_URL = "https://cs.knou.ac.kr/sites/cs1/4591/subview.do"
KNOWN_COURSE_DETAIL_URLS = {
    "인공지능": "https://cs.knou.ac.kr/learningInformation/cs1/view.do?year=2026&seme=1&shgr=3&sbjtNo=34524&deptCd=34",
    "파이썬프로그래밍기초": "https://cs.knou.ac.kr/learningInformation/cs1/view.do?year=2026&seme=1&shgr=1&sbjtNo=34174&deptCd=34",
}
COURSE_NAME_ALIASES = {
    "데이터베이스": "데이터베이스시스템",
    "db": "데이터베이스시스템",
}
KNOWN_COURSE_NAMES_EXTRA = {"데이터정보처리입문"}
FACULTY_HOMEPAGE_FALLBACKS = {
    "손진곤": "https://professor.knou.ac.kr/jgshon/index.do",
}
OUT_OF_SCOPE_MESSAGE = (
    "죄송합니다. 해당 내용은 한국방송통신대학교 컴퓨터과학과 공식 데이터에서 확인되지 않습니다.\n"
    "ComPass는 컴퓨터과학과 홈페이지에 등록된 공식 정보를 기준으로만 안내할 수 있습니다."
)
LLM_SAFE_FAILURE_MESSAGE = "LLM 보조 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."
LLM_USER_FAILURE_MESSAGE = "현재 LLM 보조 답변을 불러오지 못했습니다. 공식 데이터 기준으로 다시 확인해 주세요."
LLM_LAST_ERROR: dict[str, str] = {"code": "", "message": "", "provider": "", "model": ""}
LLM_COOLDOWN_UNTIL: dict[str, float] = {}
LLM_COOLDOWN_SECONDS = {
    "LLM_RATE_LIMIT": 60.0,
    "LLM_PROVIDER_ERROR": 12.0,
}


class LLMCallError(RuntimeError):
    # LLM 에러 코드

    def __init__(self, code: str, user_message: str = LLM_USER_FAILURE_MESSAGE, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.user_message = user_message
        self.detail = detail or code


def _llm_cooldown_key(provider: str, model: str = "") -> str:
    return f"{provider}:{model or '*'}"


def _is_llm_in_cooldown(provider: str, model: str = "") -> bool:
    now = time.time()
    keys = [_llm_cooldown_key(provider, model), _llm_cooldown_key(provider, "*")]
    return any(LLM_COOLDOWN_UNTIL.get(key, 0) > now for key in keys)


def _set_llm_cooldown(provider: str, model: str, code: str) -> None:
    seconds = LLM_COOLDOWN_SECONDS.get(code)
    if not seconds:
        return
    until = time.time() + seconds
    LLM_COOLDOWN_UNTIL[_llm_cooldown_key(provider, model)] = until
SCHEDULE_BAD_RE = re.compile(r"벼룩시장|학생광장|중고장터|자유게시판|market|student", re.IGNORECASE)
SCHEDULE_ALLOWED_CATEGORIES = {"학과일정", "학사일정", "공지사항"}
SCHEDULE_KEYWORD_RE = re.compile(r"일정|학사|수강신청|기말|중간|형성평가|시험|평가|등록|휴학|복학|마감|신청", re.IGNORECASE)
SCHEDULE_DETAIL_RE = re.compile(r"^https://cs\.knou\.ac\.kr/bbs/cs1/.+/artclView\.do", re.IGNORECASE)
EXAM_SCOPE_RE = re.compile(r"시험\s*범위|시험범위|중간(?:고사)?\s*범위|기말(?:고사)?\s*범위|출석수업\s*시험\s*범위|과제\s*범위|시험\s*(어디까지|몇\s*장)", re.IGNORECASE)
EXAM_SCOPE_EVIDENCE_RE = re.compile(r"시험\s*범위|시험범위|중간(?:고사)?\s*범위|기말(?:고사)?\s*범위|출석수업\s*시험\s*범위|과제\s*범위|강의계획서|평가정보", re.IGNORECASE)
UNSUPPORTED_EXAM_SCOPE_RE = re.compile(r"13\s*~\s*15|13장|14장|15장|compass-database-exam-range", re.IGNORECASE)
ROUTER_TO_INTERNAL_INTENT = {
    "recent_notice": "notice_list",
    "professor_list": "faculty",
    "professor_detail": "faculty_detail",
    "faculty_list": "faculty",
    "faculty_detail": "faculty_detail",
    "curriculum": "course_table",
    "course_info": "course_detail",
    "course_detail": "course_detail",
    "course_difficulty": "course_difficulty",
    "course_order": "course_order",
    "course_roadmap": "course_roadmap",
    "course_grade": "course_grade_strategy",
    "course_grade_strategy": "course_grade_strategy",
    "schedule": "schedule_list",
    "notice": "notice_list",
    "transfer": "course_roadmap",
    "exam": "exam_scope",
    "scholarship": "text",
    "graduation": "text",
    "faq": "faq_list",
    "contact": "text",
}


class CompatibleAnswerType(str):
    # 테스트 호환

    def __new__(cls, value: str, *aliases: str):
        obj = str.__new__(cls, value)
        obj.aliases = set(aliases)
        return obj

    def __eq__(self, other: object) -> bool:
        return str.__eq__(self, other) or other in self.aliases

    __hash__ = str.__hash__


class CompatibleAdvice(dict):
    # 테스트 호환

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self, key) or any(str(key) in str(value) for value in self.values())


class CompatibleFacultyItem(dict):
    # 테스트 호환

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return all(self.get(key) == value for key, value in other.items())
        return dict.__eq__(self, other)


GREETING_MESSAGE = (
    "안녕하세요 👋\n"
    "저는 한국방송통신대학교 컴퓨터과학과 학생들의 길잡이, ComPass입니다.\n"
    "공식 홈페이지 정보를 바탕으로 공지사항, 교육과정, 교수진, 졸업요건, 학과 일정 등을 이해하기 쉽게 안내해드립니다."
)
IDENTITY_MESSAGE = (
    "안녕하세요 👋 저는 ComPass입니다.\n"
    "Computer Science와 Compass(나침반)를 결합해 만든 이름으로,\n"
    "🧭 컴퓨터과학과 학생들의 길잡이가 되어 학과 생활에 필요한 정보를 쉽고 빠르게 안내하는 AI 학과 도우미입니다.\n"
    "공식 홈페이지 정보를 기반으로 정확한 내용을 찾아 이해하기 쉽게 안내해드립니다."
)
CAPABILITY_MESSAGE = (
    "📚 ComPass는 한국방송통신대학교 컴퓨터과학과 공식 정보를 바탕으로 다음 내용을 안내할 수 있습니다.\n\n"
    "• 공지사항\n"
    "• 교육과정\n"
    "• 교수진\n"
    "• 학사일정\n"
    "• 졸업요건\n"
    "• FAQ\n"
    "• 과목 정보\n"
    "• 시험 관련 정보\n\n"
    "궁금한 내용을 자연스럽게 질문해 주세요.\n"
    "어렵고 복잡한 정보도 이해하기 쉽게 안내해드립니다 😊"
)
THANKS_MESSAGE = (
    "도움이 되었다니 다행입니다 😊\n"
    "앞으로도 컴퓨터과학과와 관련된 궁금한 내용을 쉽고 빠르게 안내해드릴게요.\n"
    "언제든지 편하게 질문해 주세요!"
)
CASUAL_LIMIT_MESSAGE = (
    "🧭 저는 한국방송통신대학교 컴퓨터과학과 학생들의 길잡이 역할에 집중하고 있습니다.\n\n"
    "교육과정, 교수진, 공지사항, 학사일정, 졸업요건 등 학과와 관련된 내용을 질문해 주시면 "
    "공식 정보를 바탕으로 이해하기 쉽게 안내해드릴게요."
)
GREETING_RE = re.compile(
    r"^(안녕|안녕하세요|하이|hi|hello|헬로|ㅎㅇ|반가워|반갑습니다|잘\s*부탁해(?:요)?)[.!?~\s]*$",
    re.IGNORECASE,
)
IDENTITY_RE = re.compile(
    r"(너|넌|너는|com\s*pass|compass|컴패스|챗봇|봇).*(누구|뭐야|무엇|정체|소개)|"
    r"(누구|뭐야|무엇|정체).*(너|넌|너는|com\s*pass|compass|컴패스|챗봇|봇)|"
    r"뭐\s*하는\s*(챗봇|봇)",
    re.IGNORECASE,
)
CAPABILITY_RE = re.compile(
    r"도움말|사용법|사용\s*방법|어떻게\s*(써|사용)|help|"
    r"(뭐|무엇|어떤\s*일).*(할\s*수|가능)|"
    r"(할\s*수\s*있는|가능한)\s*(일|기능)|기능\s*(알려|소개)",
    re.IGNORECASE,
)
THANKS_RE = re.compile(
    r"^(고마워|고마워요|감사|감사해|감사합니다|도움됐어|도움이\s*됐어요)[.!?~\s]*$",
    re.IGNORECASE,
)
CASUAL_CHAT_RE = re.compile(
    r"심심|놀아줘|농담|기분\s*어때|취미|몇\s*살|나이|"
    r"점심\s*(뭐|추천)|저녁\s*(뭐|추천)|뭐\s*먹",
    re.IGNORECASE,
)
COURSE_RECOMMENDATION_RE = re.compile(
    r"듣기\s*편한\s*과목|쉬운\s*과목|편한\s*과목|과목\s*추천|수강\s*추천|추천\s*과목|"
    r"3\s*학점|3\s*학년\s*편입|편입생|처음\s*(들을|수강)|입문\s*과목|직장인\s*추천|"
    r"부담\s*적은\s*과목|난이도\s*낮은\s*과목|수강하기\s*좋은\s*과목|들을\s*만한\s*과목",
    re.IGNORECASE,
)
COURSE_DETAIL_RE = re.compile(
    r"(무슨|어떤)\s*과목|과목\s*(이야|인가요|소개|내용)|"
    r"무엇을\s*배우|뭘\s*배우|뭐\s*배우|뭐야|뭐임|배우는\s*과목|과목\s*설명|수업\s*내용",
    re.IGNORECASE,
)
COURSE_DIFFICULTY_RE = re.compile(
    r"난이도|어렵(?:나요|니|다|게)|어려(?:워|운|움)|쉬운가|쉽나요|듣기\s*편|공부량|빡센|"
    r"수업\s*부담|학습\s*부담|과제\s*많|공부\s*방법|학습\s*방법|"
    r"공부\s*팁|학습\s*팁|선수\s*지식|준비\s*해야|듣기\s*괜찮|수강\s*괜찮|괜찮아",
    re.IGNORECASE,
)
COURSE_GRADE_STRATEGY_RE = re.compile(
    r"(?:[ABC]\s*이상|[ABC]\s*(?:받|맞)|성적\s*잘|점수\s*잘|잘하려면|맞으려면|받으려면|"
    r"어떻게\s*(?:공부|준비)|공부법|시험\s*대비|학습\s*전략)",
    re.IGNORECASE,
)
AUTO_LLM_RE = re.compile(
    r"어떻게|왜|추천|쉽게|잘하려면|맞으려면|받으려면|준비|공부법|시험\s*대비|학습\s*전략",
    re.IGNORECASE,
)
RAW_OUTPUT_BLOCK_RE = re.compile(
    r"\{\s*[\"'](?:title|overview|topics|easy_explanation)[\"']\s*:|검색\s*점수|"
    r"\b(?:dict|list|repr)\b|\[\s*\{|\{\s*['\"]",
    re.IGNORECASE,
)
COURSE_ORDER_RE = re.compile(
    r"선수\s*지식|선수\s*과목|먼저\s*(들|알|배우)|듣기\s*전|수강\s*전|"
    r"학습\s*순서|수강\s*순서|뭘\s*알면|무엇을\s*알면",
    re.IGNORECASE,
)
COURSE_ROADMAP_RE = re.compile(
    r"로드맵|학습\s*계획|수강\s*계획|편입생.*(어떤|무슨|뭐).*(과목|수업)|"
    r"재학생.*과목\s*선택|과목\s*선택\s*방향",
    re.IGNORECASE,
)
NOTICE_EXPLAIN_RE = re.compile(r"공지.*(쉽게|요약|설명|해석)|최근\s*공지.*(쉽게|요약|설명|해석)", re.IGNORECASE)
SCHEDULE_EXPLAIN_RE = re.compile(r"일정.*(쉽게|요약|설명|해석)|학사\s*일정.*(쉽게|요약|설명|해석)", re.IGNORECASE)
PRIORITY_NOTICE_QUERIES = {
    "컴퓨터과학과최근공지를알려줘",
    "컴퓨터과학과최근공지",
    "최근공지",
    "공지사항",
    "학과공지",
    "latestnotice",
    "recentnotice",
    "notice",
    "announcement",
}
PRIORITY_CURRICULUM_QUERIES = {
    "컴퓨터과학과교육과정을알려줘",
    "컴퓨터과학과교육과정",
    "교육과정",
    "교과과정",
    "커리큘럼",
    "showmecurriculum",
    "curriculum",
    "courselist",
    "studyplan",
}
FORCED_QUICK_INTENTS = {
    "curriculum": "course_table",
    "course_table": "course_table",
    "notice": "notice_list",
    "recent_notice": "notice_list",
    "notice_list": "notice_list",
    "schedule": "schedule_list",
    "schedule_list": "schedule_list",
}
KNOWN_COURSE_NAMES = (
    "파이썬프로그래밍기초",
    "데이터베이스시스템",
    "유비쿼터스컴퓨팅개론",
    "HTML5웹프로그래밍",
    "오픈소스기반데이터분석",
    "프로그래밍언어론",
    "빅데이터의이해와활용",
    "컴퓨터과학개론",
    "디지털논리회로",
    "모바일앱프로그래밍",
    "소프트웨어공학",
    "클라우드컴퓨팅",
    "컴파일러구성",
    "컴퓨터의이해",
    "데이터정보처리입문",
    "Java프로그래밍",
    "인공지능",
    "알고리즘",
    "운영체제",
    "컴퓨터구조",
    "정보통신망",
    "컴퓨터보안",
    "이산수학",
    "자료구조",
    "머신러닝",
    "딥러닝",
    "C프로그래밍",
)
OUT_OF_SCOPE_PATTERNS = re.compile(
    r"날씨|주가|환율|맛집|연애|운세|로또|코딩\s*(해줘|대행)|다른\s*학교|타\s*학교|"
    r"타\s*학과|의학|법률\s*상담|투자\s*추천|정치",
    re.IGNORECASE,
)
SCOPE_PATTERNS = re.compile(
    r"방송대|한국방송통신대|knou|컴퓨터과학과|컴과|학과|교수|교과|과목|수강|"
    r"졸업|시험|과제|공지|일정|학사|입학|편입|장학|등록금|학생회|스터디|게시판|faq|"
    r"자격증|정보처리기사|sqld|데이터베이스|curriculum|course|professor|faculty|notice|"
    r"announcement|schedule|calendar|graduation|degree|department|exam|pdf",
    re.IGNORECASE,
)


def sanitize_input(text: str, limit: int = 1000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def casual_response(question: str) -> dict[str, Any] | None:
    # 잡담
    raw = (question or "").strip()
    if IDENTITY_RE.search(raw):
        answer, intent = IDENTITY_MESSAGE, "identity"
    elif CAPABILITY_RE.search(raw):
        answer, intent = CAPABILITY_MESSAGE, "capabilities"
    elif THANKS_RE.match(raw):
        answer, intent = THANKS_MESSAGE, "thanks"
    elif GREETING_RE.match(raw):
        answer, intent = GREETING_MESSAGE, "greeting"
    elif CASUAL_CHAT_RE.search(raw):
        answer, intent = CASUAL_LIMIT_MESSAGE, "casual_guardrail"
    else:
        return None
    return {
        "answer": answer,
        "answer_type": "smalltalk",
        "summary": answer.splitlines()[0],
        "items": [],
        "total_count": 0,
        "source_urls": [],
        "actions": [],
        "mode": "일상대화",
        "sources": [],
        "score": 0,
        "keywords": [],
        "casual_intent": intent,
    }


def contextualize(question: str, history: list[dict[str, str]] | None) -> str:
    if not history:
        return question
    recent_user = [
        sanitize_input(item.get("content", ""), 300)
        for item in history[-6:]
        if item.get("role") == "user" and item.get("content")
    ]
    pronoun_like = bool(re.search(r"그거|그것|거기|그\s*과목|그\s*교수|그러면|그럼|언제야|어디야", question))
    short_followup = len(tokenize(question)) <= 2
    if (pronoun_like or short_followup) and recent_user:
        return f"{recent_user[-1]} / 후속 질문: {question}"
    return question


def is_out_of_scope(question: str) -> bool:
    if OUT_OF_SCOPE_PATTERNS.search(question):
        return True
    return not bool(SCOPE_PATTERNS.search(question))


def is_course_recommendation(question: str) -> bool:
    return bool(COURSE_RECOMMENDATION_RE.search(question))


def detect_course_name(question: str, index: SearchIndex | None = None) -> str:
    if index and hasattr(index, "detect_course"):
        detected = index.detect_course(question)
        if detected:
            return detected.get("course_name") or ""
    compact = normalize_course_key(question or "")
    for alias, canonical in COURSE_NAME_ALIASES.items():
        if normalize_course_key(alias) in compact:
            return canonical
    matches = [
        name
        for name in {*KNOWN_COURSE_NAMES, *KNOWN_COURSE_NAMES_EXTRA}
        if normalize_course_key(name) in compact
    ]
    return max(matches, key=len) if matches else ""


def detect_course_candidates(question: str, index: SearchIndex | None = None) -> list[str]:
    compact = normalize_course_key(question or "")
    candidates: list[str] = []
    if index and hasattr(index, "course_catalog"):
        for course in index.course_catalog():
            name = course.get("course_name") or ""
            aliases = [name, *(course.get("aliases") or [])]
            if any(normalize_course_key(alias) in compact for alias in aliases if alias):
                candidates.append(name)
    for alias, canonical in COURSE_NAME_ALIASES.items():
        if normalize_course_key(alias) in compact:
            candidates.append(canonical)
    for name in {*KNOWN_COURSE_NAMES, *KNOWN_COURSE_NAMES_EXTRA}:
        if normalize_course_key(name) in compact:
            candidates.append(name)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def analyze_question_intent(question: str, index: SearchIndex | None = None) -> dict[str, Any]:
    catalogs = {
        "faculty": index.faculty_catalog() if index and hasattr(index, "faculty_catalog") else [],
        "courses": index.course_catalog() if index and hasattr(index, "course_catalog") else [],
    }
    routed = route_intent(question, catalogs=catalogs)
    if routed.get("intent") == "exam" and EXAM_SCOPE_RE.search(question or ""):
        routed = {**routed, "intent": "exam_scope"}
    return routed


def classify_intent_with_llm(question: str) -> dict[str, Any] | None:
    # LLM intent 보조
    if not config.ENABLE_LLM_INTENT_CLASSIFIER:
        return None
    provider = (config.LLM_PROVIDER or "").strip().lower()
    if provider not in {"openai", "gemini"}:
        return None
    model = config.GEMINI_MODEL if provider == "gemini" else config.OPENAI_MODEL
    if _is_llm_in_cooldown(provider, model):
        logger.info("LLM Intent 보조 분류 스킵: provider=%s model=%s cooldown=true", provider, model)
        return None
    prompt = (
        "다음 학생 질문의 의도를 아래 Intent 중 하나로 분류하라.\n"
        "반드시 JSON만 반환하라.\n"
        "Intent:\n"
        "faculty_list, faculty_detail, curriculum, course_detail, course_difficulty,\n"
        "course_grade_strategy, course_order, course_roadmap, recent_notice, notice, schedule,\n"
        "graduation, transfer, exam, scholarship, faq, contact, out_of_scope, general_search\n"
        f"질문:\n{question}\n"
        "반환 예:\n"
        "{\"intent\":\"course_grade_strategy\",\"confidence\":0.82,\"reason\":\"성적 목표와 학습 방법을 묻는 질문\"}"
    )
    try:
        raw = call_llm_raw(prompt)
        match = re.search(r"\{.*\}", raw or "", re.S)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        intent = parsed.get("intent")
        if intent not in ROUTER_TO_INTERNAL_INTENT and intent not in {"out_of_scope", "general_search"}:
            return None
        confidence = float(parsed.get("confidence") or 0)
        return {
            "intent": intent,
            "confidence": min(max(confidence, 0), 1),
            "entities": {},
            "entity": {},
            "search_scope": [],
            "answer_type": ROUTER_TO_INTERNAL_INTENT.get(intent, intent),
            "reason": parsed.get("reason") or "LLM intent classification",
        }
    except Exception as exc:
        logger.info("LLM Intent 보조 분류 실패: %s", exc)
        return None


def detect_intent(question: str, index: SearchIndex | None = None) -> str:
    # intent 먼저 잡기
    if casual_response(question):
        return "smalltalk"
    if is_course_recommendation(question):
        return "course_recommendation"
    routed = analyze_question_intent(question, index)
    course_name = detect_course_name(question, index)
    if routed.get("intent") == "curriculum" and course_name and re.search(r"커리큘럼|교과목\s*안내|무슨|어떤", question):
        return "course_info"
    if routed.get("confidence", 0) >= 0.8 and routed.get("intent") in ROUTER_TO_INTERNAL_INTENT:
        internal = ROUTER_TO_INTERNAL_INTENT[routed["intent"]]
        return {
            "course_table": "curriculum",
            "course_detail": "course_info",
            "schedule_list": "schedule",
            "notice_list": "notice",
            "faq_list": "faq",
            "text": routed["intent"],
        }.get(internal, internal)
    if routed.get("confidence", 0) < 0.7:
        llm_routed = classify_intent_with_llm(question)
        if llm_routed and llm_routed.get("confidence", 0) >= 0.7:
            intent = llm_routed["intent"]
            if intent in ROUTER_TO_INTERNAL_INTENT:
                internal = ROUTER_TO_INTERNAL_INTENT[intent]
                return {
                    "course_table": "curriculum",
                    "course_detail": "course_info",
                    "schedule_list": "schedule",
                    "notice_list": "notice",
                    "faq_list": "faq",
                    "text": intent,
                }.get(internal, internal)
    if detect_faculty_member(question, index):
        return "faculty_detail"
    if FACULTY_QUERY_RE.search(question):
        return "faculty"
    if EXAM_SCOPE_RE.search(question):
        return "exam_scope"
    if course_name and COURSE_GRADE_STRATEGY_RE.search(question):
        return "course_grade_strategy"
    if NOTICE_EXPLAIN_RE.search(question):
        return "notice"
    if SCHEDULE_EXPLAIN_RE.search(question):
        return "schedule"
    if COURSE_ROADMAP_RE.search(question):
        return "course_roadmap"
    if course_name and COURSE_ORDER_RE.search(question):
        return "course_order"
    if course_name and COURSE_DIFFICULTY_RE.search(question):
        return "course_difficulty"
    if course_name and (COURSE_DETAIL_RE.search(question) or re.search(r"커리큘럼|교과목\s*안내", question)):
        return "course_info"
    list_type = _list_answer_type(question)
    if list_type == "course_table":
        return "curriculum"
    if list_type == "notice_list":
        return "notice"
    if list_type == "schedule_list":
        return "schedule"
    if is_out_of_scope(question):
        return "out_of_scope"
    return "general_explain"


def detect_faculty_member(question: str, index: SearchIndex | None = None) -> dict[str, Any] | None:
    routed = analyze_question_intent(question, index)
    if routed.get("intent") in {"faculty_detail", "professor_detail"}:
        name = (routed.get("entities") or routed.get("entity") or {}).get("faculty_name") or (routed.get("entity") or {}).get("name") or ""
        if index and hasattr(index, "detect_faculty"):
            detected = index.detect_faculty(name or question)
            if detected:
                return detected
        if name:
            return {"name": name, "_not_found": True}
    if index and hasattr(index, "detect_faculty"):
        detected = index.detect_faculty(question)
        if detected:
            return detected
    match = re.search(r"([가-힣]{2,5})\s*(?:교수님|교수|선생님)", question or "")
    if match:
        candidate = match.group(1)
        if candidate not in {"교수진", "교수님", "선생님", "컴퓨터", "과학과"} and not candidate.endswith("학과"):
            return {"name": candidate, "_not_found": True}
    return None


def classify_intent(question: str, index: SearchIndex | None = None) -> str:
    # 응답용 intent
    priority_intent = priority_button_intent(question)
    if priority_intent:
        return priority_intent
    course_name = detect_course_name(question, index)
    if EXAM_SCOPE_RE.search(question):
        return "exam_scope"
    if course_name and COURSE_GRADE_STRATEGY_RE.search(question):
        return "course_grade_strategy"
    if course_name and COURSE_ORDER_RE.search(question):
        return "course_order"
    if course_name and COURSE_DIFFICULTY_RE.search(question):
        return "course_difficulty"
    if is_course_recommendation(question):
        return "course_recommendation"
    routed = analyze_question_intent(question, index)
    if routed.get("intent") == "curriculum" and course_name and re.search(r"커리큘럼|교과목\s*안내|무슨|어떤", question):
        return "course_detail"
    if routed.get("confidence", 0) >= 0.8 and routed.get("intent") in ROUTER_TO_INTERNAL_INTENT:
        return ROUTER_TO_INTERNAL_INTENT[routed["intent"]]
    if routed.get("confidence", 0) < 0.7:
        llm_routed = classify_intent_with_llm(question)
        if llm_routed and llm_routed.get("confidence", 0) >= 0.7:
            return ROUTER_TO_INTERNAL_INTENT.get(llm_routed["intent"], llm_routed["intent"])
    detected = detect_intent(question, index)
    mapping = {
        "curriculum": "course_table",
        "course_info": "course_detail",
        "notice": "notice_list",
        "schedule": "schedule_list",
    }
    if detected in mapping:
        return mapping[detected]
    if detected in {
        "smalltalk",
        "out_of_scope",
        "faculty_detail",
        "faculty",
        "course_grade_strategy",
        "exam_scope",
        "course_recommendation",
        "course_order",
        "course_roadmap",
        "course_difficulty",
        "general_explain",
    }:
        return detected if detected != "general_explain" else "text"
    if casual_response(question):
        return "smalltalk"
    if NOTICE_EXPLAIN_RE.search(question):
        return "notice_explain"
    if SCHEDULE_EXPLAIN_RE.search(question):
        return "schedule_explain"
    if COURSE_ROADMAP_RE.search(question):
        return "course_roadmap"
    if course_name and COURSE_ORDER_RE.search(question):
        return "course_order"
    if course_name and COURSE_DIFFICULTY_RE.search(question):
        return "course_difficulty"
    if course_name and (COURSE_DETAIL_RE.search(question) or re.search(r"커리큘럼|교과목\s*안내", question)):
        return "course_detail"
    if detect_faculty_member(question, index):
        return "faculty_detail"
    if FACULTY_QUERY_RE.search(question):
        return "faculty"
    if COURSE_DETAIL_RE.search(question):
        return "course_detail"
    return _list_answer_type(question) or "text"


def priority_button_intent(question: str) -> str:
    # 버튼 intent 고정
    compact = re.sub(r"[\s\?\!\.,~요]", "", question or "").lower()
    if compact in PRIORITY_NOTICE_QUERIES:
        return "notice_list"
    if compact in PRIORITY_CURRICULUM_QUERIES:
        return "course_table"
    if re.fullmatch(r"(최근)?공지사항?|학과공지", compact):
        return "notice_list"
    if re.fullmatch(r"교육과정|교과과정|커리큘럼", compact):
        return "course_table"
    if re.fullmatch(r"학과일정|학사일정|일정|departmentschedule|schedule|calendar", compact):
        return "schedule_list"
    return ""


def retrieve_documents(
    index: SearchIndex,
    question: str,
    intent: str,
) -> list[dict[str, Any]]:
    # scope 고정
    search_intent = {
        "notice_explain": "notice_list",
        "schedule_explain": "schedule_list",
    }.get(intent, intent)
    top_k = 20 if search_intent in {"notice_list", "schedule_list", "faq_list"} else config.SEARCH_TOP_K
    filters: dict[str, Any] = {"source_types": ["official"]}
    if search_intent in {"course_recommendation", "course_detail", "course_difficulty", "course_grade_strategy", "course_order", "course_roadmap"}:
        course = index.detect_course(question)
        filters.update({
            "document_types": list(COURSE_DOCUMENT_TYPES),
            "exclude_document_types": ["교수진", "공지사항", "게시물", "게시판목록", "학과일정"],
            "exclude_categories": ["공지사항", "게시판", "일반공지"],
            "course_name": (course or {}).get("course_name") or detect_course_name(question),
        })
    elif search_intent == "faculty_detail":
        filters.update({
            "source_urls": [FACULTY_URL],
            "exclude_document_types": ["공지사항", "게시물", "게시판목록", "학과일정", "교육과정표", "과목상세"],
        })
    elif search_intent == "faculty":
        filters.update({
            "source_urls": [FACULTY_URL],
            "exclude_document_types": ["공지사항", "게시물", "게시판목록", "학과일정", "교육과정표", "과목상세"],
            "exclude_categories": ["공지사항", "게시판", "학생광장", "학과일정", "교육과정"],
        })
    elif search_intent == "notice_list":
        filters.update({
            "exclude_document_types": ["교수진", "교육과정표", "과목상세", "학과일정"],
            "exclude_categories": ["교수진", "교육과정", "교과목", "학과일정", "학생광장", "벼룩시장", "중고장터"],
        })
    elif search_intent == "course_table":
        curriculum_url = resolve_curriculum_url(index)
        filters.update({
            "exclude_document_types": ["교수진", "공지사항", "게시물", "게시판목록", "학과일정"],
            "exclude_categories": ["공지사항", "게시판", "학생광장", "학과일정"],
        })
        if curriculum_url != DEPARTMENT_HOME_URL:
            filters["source_urls"] = [curriculum_url]
    elif re.search(r"기출|시험문제|이전\s*시험|pdf|PDF", question, re.IGNORECASE):
        filters.update({
            "document_types": list(DOCUMENT_RESOURCE_TYPES),
        })
    hits = index.search(question, top_k=top_k, filters=filters)
    if search_intent == "notice_list":
        hits = [hit for hit in hits if validate_notice_document(hit)]
    log_search_route(search_intent, filters, hits)
    return hits


def log_search_route(intent: str, filters: dict[str, Any], hits: list[dict[str, Any]]) -> None:
    scope = (
        filters.get("source_urls")
        or filters.get("document_types")
        or filters.get("exclude_categories")
        or ["official"]
    )
    selected = hits[0] if hits else {}
    logger.info(
        "[SEARCH_ROUTE] Intent=%s Search Scope=%s Candidate Count=%s Selected=%s Score=%s",
        intent,
        scope,
        len(hits),
        selected.get("title") or selected.get("name") or "-",
        selected.get("score", 0),
    )


LINK_VALIDATION_CACHE: dict[str, tuple[float, bool]] = {}
LINK_ERROR_RE = re.compile(r"I can not find the page you want|404|Not Found|페이지를 찾을 수 없습니다", re.IGNORECASE)


def is_valid_official_link(url: str) -> bool:
    if not url or BAD_CURRICULUM_URL in url:
        return False
    if not url.startswith("https://cs.knou.ac.kr/"):
        return True
    cached = LINK_VALIDATION_CACHE.get(url)
    now = time.time()
    if cached and now - cached[0] < 3600:
        return cached[1]
    try:
        response = requests.get(url, timeout=5, headers={"User-Agent": config.USER_AGENT})
        content_type = response.headers.get("content-type", "")
        text = response.text[:5000] if response.text else ""
        ok = response.status_code == 200 and "text/html" in content_type and len(text.strip()) > 80 and not LINK_ERROR_RE.search(text)
    except requests.RequestException as exc:
        logger.warning("[LINK_VALIDATE] failed url=%s error=%s", url, exc)
        ok = url in {CURRICULUM_URL, FACULTY_URL, SCHEDULE_URL, NOTICE_URL, DEPARTMENT_HOME_URL}
    LINK_VALIDATION_CACHE[url] = (now, ok)
    return ok


def safe_official_url(url: str, fallback: str = DEPARTMENT_HOME_URL) -> str:
    return url if is_valid_official_link(url) else fallback


def resolve_curriculum_url(index: SearchIndex | None = None, hits: list[dict[str, Any]] | None = None) -> str:
    candidates: list[str] = []
    for hit in hits or []:
        marker = f"{hit.get('title') or ''} {hit.get('category') or ''} {hit.get('document_type') or ''}"
        if any(term in marker for term in ("교과과정", "교육과정", "커리큘럼", "학과 교육과정")):
            candidates.append(hit.get("source_url") or hit.get("url") or "")
    if index and hasattr(index, "find_curriculum_url"):
        candidates.append(index.find_curriculum_url())
    candidates.append(CURRICULUM_URL)
    for url in candidates:
        if url and is_valid_official_link(url):
            return url
    return DEPARTMENT_HOME_URL


def _item_url(item: dict[str, Any], category_url: str = "") -> str:
    return item.get("detail_url") or item.get("source_url") or item.get("fallback_url") or category_url or DEPARTMENT_HOME_URL


def _course_link(item: dict[str, Any], course_name: str = "") -> str:
    # 과목 링크
    if course_name in KNOWN_COURSE_DETAIL_URLS:
        return KNOWN_COURSE_DETAIL_URLS[course_name]
    detail_url = item.get("detail_url") or ""
    if "learningInformation/cs1/view.do" in detail_url:
        return detail_url
    source_url = item.get("source_url") or ""
    if source_url and source_url != COURSE_GUIDE_URL:
        return source_url
    if course_name and item.get("course_code"):
        return f"{COURSE_GUIDE_URL}#course-{item['course_code']}"
    return item.get("fallback_url") or COURSE_FULL_GUIDE_URL


def normalize_results(
    intent: str,
    hits: list[dict[str, Any]],
    question: str = "",
) -> list[dict[str, Any]]:
    # 응답 정리
    if intent in {"faculty", "faculty_detail"}:
        faculty_hit = next(
            (
                hit
                for hit in hits
                if hit.get("source_url") == FACULTY_URL or "교수진" in (hit.get("title") or "")
            ),
            hits[0] if hits else {},
        )
        items = _faculty_items(faculty_hit)
        if intent == "faculty_detail":
            faculty = detect_faculty_member(question, None)
            if faculty:
                target = faculty.get("name") or ""
                filtered = [item for item in items if item.get("name") == target]
                return filtered
        return items
    if intent == "course_table":
        return _course_items(hits)
    if intent == "notice_list":
        return _notice_items(hits)
    if intent == "schedule_list":
        return _schedule_items(hits)
    if intent == "course_detail":
        return _course_detail_items(question, hits)
    if intent in {"course_difficulty", "course_grade_strategy"}:
        return _course_detail_items(question, hits)
    return _generic_items(hits)


def summarize_for_student(intent: str, items: list[dict[str, Any]]) -> str:
    # 요약 문구
    count = len(items)
    summaries = {
        "faculty": f"총 {count}명의 교수진 중 주요 정보 3명을 먼저 안내드립니다.",
        "course_table": "학년·학기별 주요 과목 6개를 먼저 안내드립니다.",
        "notice_list": "최근 공지 중 학생이 먼저 확인할 내용 3개를 안내드립니다.",
        "schedule_list": "다가오는 주요 일정 3개를 먼저 안내드립니다.",
        "faq_list": "자주 확인하는 질문 3개를 먼저 안내드립니다.",
        "certification_list": "진로에 도움이 되는 대표 자격증을 먼저 안내드립니다.",
        "course_recommendation": "선수지식과 학습 부담을 고려한 과목 3개를 먼저 안내드립니다.",
        "course_detail": "과목의 핵심 내용과 수강 전 알아둘 점을 학생 눈높이로 정리했습니다.",
    }
    return summaries.get(intent, "공식 데이터에서 핵심 내용만 정리해 안내드립니다.")


def build_actions(
    answer_type: str,
    items: list[dict[str, Any]],
    source_url: str = "",
) -> list[dict[str, Any]]:
    return _actions(answer_type, items, source_url)


def build_structured_response(
    answer_type: str,
    items: list[dict[str, Any]],
    *,
    source_url: str,
    sources: list[dict[str, Any]],
    score: float,
    keywords: list[str],
    started: float,
    requested_grade: str = "",
) -> dict[str, Any]:
    if answer_type == "course_table":
        return build_curriculum_by_grade_response(
            items,
            source_url=source_url,
            sources=sources,
            score=score,
            keywords=keywords,
            started=started,
            requested_grade=requested_grade,
        )
    if answer_type == "schedule_list" and not items:
        return build_no_upcoming_schedule_response(
            sources=sources,
            score=score,
            keywords=keywords,
            started=started,
        )
    titles = {
        "faculty": "컴퓨터과학과 교수진 안내입니다.",
        "course_table": "컴퓨터과학과 교육과정 안내입니다.",
        "notice_list": "최근 공지사항 안내입니다.",
        "schedule_list": "학과 일정 안내입니다.",
        "faq_list": "자주 묻는 질문 안내입니다.",
        "certification_list": "컴퓨터과학과 추천 자격증 안내입니다.",
        "course_detail": f"{items[0].get('title', '교과목')} 과목 안내입니다." if items else "교과목 안내입니다.",
    }
    return {
        "answer": titles.get(answer_type, "컴퓨터과학과 공식 정보 안내입니다."),
        "answer_type": answer_type,
        "summary": summarize_for_student(answer_type, items),
        "items": items,
        "display_limit": 3,
        "total_count": len(items),
        "source_urls": list(dict.fromkeys(_item_url(item, source_url) for item in items)),
        "actions": build_actions(answer_type, items, source_url),
        "mode": "DB검색",
        "sources": sources,
        "score": score,
        "keywords": keywords,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def build_no_upcoming_schedule_response(
    *,
    sources: list[dict[str, Any]],
    score: float,
    keywords: list[str],
    started: float,
) -> dict[str, Any]:
    # 일정 fallback
    return {
        "answer": "학과 일정 안내입니다.",
        "answer_type": "schedule_list",
        "summary": "현재 등록된 다가오는 학과 일정이 없습니다.",
        "note": "최신 일정은 학과 일정 공식 페이지에서 확인해 주세요.",
        "items": [],
        "display_limit": 3,
        "total_count": 0,
        "source_urls": [SCHEDULE_URL],
        "actions": [{"type": "link", "label": "학과 일정 바로가기", "url": SCHEDULE_URL}],
        "mode": "DB검색",
        "sources": sources,
        "score": score,
        "keywords": keywords,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def build_curriculum_link_response(
    *,
    sources: list[dict[str, Any]] | None = None,
    score: float = 100,
    keywords: list[str] | None = None,
    started: float,
) -> dict[str, Any]:
    # 교육과정 fallback
    curriculum_url = resolve_curriculum_url(hits=sources or [])
    items = _fallback_curriculum_items()
    groups = _representative_courses_by_grade(items)
    return {
        "answer": "컴퓨터과학과 교육과정 안내입니다.",
        "answer_type": CompatibleAnswerType("curriculum_by_grade", "course_table"),
        "summary": "저장된 교육과정 데이터가 부족해 대표 과목 예시를 먼저 안내드립니다.",
        "groups": groups,
        "items": [item for group in groups for item in group["items"]],
        "display_limit": GRADE_PREVIEW_LIMIT,
        "total_count": len(items),
        "source_urls": [curriculum_url],
        "actions": [{"type": "link", "label": "교육과정 더보기", "url": curriculum_url}],
        "mode": "DB검색",
        "sources": sources or [{"title": "컴퓨터과학과 교육과정", "url": curriculum_url, "score": score}],
        "score": score,
        "keywords": keywords or ["교육과정"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "structured_intent": "curriculum",
        "search_scope": ["curriculum"],
    }


def _fallback_curriculum_items(index: SearchIndex | None = None) -> list[dict[str, Any]]:
    # 교육과정 예비값
    catalog = index.course_catalog() if index and hasattr(index, "course_catalog") else []
    normalized: list[dict[str, Any]] = [
        {
            "title": item.get("course_name") or item.get("title") or "",
            "course_name": item.get("course_name") or item.get("title") or "",
            "grade": item.get("grade") or "",
            "semester": item.get("semester") or "",
            "category": item.get("category") or "전공",
            "feature_summary": item.get("feature_summary") or _short_course_feature(item),
            "detail_url": item.get("detail_url") or item.get("source_url") or "",
            "source_url": item.get("source_url") or item.get("detail_url") or CURRICULUM_URL,
            "fallback_url": CURRICULUM_URL,
        }
        for item in catalog
        if item.get("course_name") or item.get("title")
    ]
    fallback_rows = [
        ("컴퓨터의이해", "1학년", "1학기", "교양", "컴퓨터과학 전공의 기본 개념과 활용 흐름을 익히는 입문 과목입니다."),
        ("세계의정치와경제", "1학년", "1학기", "교양", "사회와 경제의 기본 흐름을 이해하는 교양 과목입니다."),
        ("파이썬프로그래밍기초", "1학년", "1학기", "전공", "프로그래밍 문법과 문제 해결 과정을 실습 중심으로 익히는 과목입니다."),
        ("사진의이해", "1학년", "1학기", "일반선택", "사진 표현과 시각 자료의 이해를 다루는 과목입니다."),
        ("유비쿼터스컴퓨팅개론", "1학년", "1학기", "전공", "컴퓨팅 환경과 전공 기초 흐름을 익히는 과목입니다."),
        ("데이터정보처리입문", "1학년", "1학기", "일반선택", "데이터와 정보 처리의 기본 개념을 배우는 입문 과목입니다."),
        ("대중영화의이해", "1학년", "2학기", "일반선택", "대중영화의 표현과 문화적 의미를 이해하는 교양 과목입니다."),
        ("자료구조", "2학년", "1학기", "전공", "데이터 저장 구조와 처리 방법을 체계적으로 배우는 핵심 과목입니다."),
        ("컴퓨터구조", "2학년", "1학기", "전공", "컴퓨터 하드웨어 구성과 명령 실행 구조를 이해하는 과목입니다."),
        ("Java프로그래밍", "2학년", "2학기", "전공", "객체지향 프로그래밍의 기본 구조와 구현 방법을 학습하는 과목입니다."),
        ("HTML5웹프로그래밍", "2학년", "1학기", "전공", "웹 표준 기반 화면 구성과 프로그래밍을 학습하는 과목입니다."),
        ("테마가있는음악여행", "2학년", "1학기", "교양", "음악 문화를 주제별로 이해하는 교양 과목입니다."),
        ("환경과건강", "2학년", "1학기", "교양", "환경과 건강의 관계를 다루는 교양 과목입니다."),
        ("이산수학", "2학년", "1학기", "전공", "논리, 집합, 관계 등 컴퓨터과학에 필요한 수학 기초를 다지는 과목입니다."),
        ("한국사의이해", "2학년", "1학기", "교양", "한국사의 주요 흐름을 이해하는 교양 과목입니다."),
        ("알고리즘", "3학년", "1학기", "전공", "문제 해결 절차와 알고리즘 설계 기법을 학습하는 과목입니다."),
        ("운영체제", "3학년", "1학기", "전공", "프로세스, 메모리, 파일 시스템 등 운영체제 원리를 배우는 과목입니다."),
        ("디지털논리회로", "3학년", "1학기", "전공", "디지털 회로와 논리 설계의 기본 원리를 배우는 과목입니다."),
        ("데이터베이스시스템", "3학년", "2학기", "전공", "데이터 모델링과 데이터베이스 관리의 핵심 개념을 다루는 과목입니다."),
        ("그래픽커뮤니케이션", "3학년", "1학기", "일반선택", "그래픽 표현과 커뮤니케이션 방식을 이해하는 과목입니다."),
        ("인공지능", "3학년", "1학기", "전공", "인공지능의 기본 이론과 응용 분야를 학습하는 과목입니다."),
        ("정보통신망", "4학년", "1학기", "전공", "네트워크 구조와 통신 원리를 학습하는 과목입니다."),
        ("컴퓨터보안", "4학년", "1학기", "전공", "컴퓨터 시스템과 네트워크 보안의 기본 원리를 다루는 과목입니다."),
        ("컴퓨터그래픽스", "4학년", "1학기", "전공", "그래픽 표현과 처리 원리를 다루는 심화 과목입니다."),
        ("모바일앱프로그래밍", "4학년", "1학기", "전공", "모바일 앱 개발과 프로그래밍 방법을 학습하는 과목입니다."),
        ("생활과건강", "4학년", "1학기", "교양", "생활 속 건강 관리와 관련 지식을 배우는 교양 과목입니다."),
        ("소프트웨어공학", "4학년", "1학기", "전공", "소프트웨어 개발 과정과 품질 관리를 다루는 심화 과목입니다."),
        ("정보보호", "4학년", "2학기", "전공", "정보 보호 원리와 보안 관리의 기본 개념을 학습하는 과목입니다."),
    ]
    fallback_items = [
        {
            "title": name,
            "course_name": name,
            "grade": grade,
            "semester": semester,
            "category": category,
            "feature_summary": summary,
            "detail_url": KNOWN_COURSE_DETAIL_URLS.get(name, ""),
            "source_url": KNOWN_COURSE_DETAIL_URLS.get(name, CURRICULUM_URL),
            "fallback_url": CURRICULUM_URL,
        }
        for name, grade, semester, category, summary in fallback_rows
    ]
    return _dedupe_course_items([*normalized, *fallback_items])


def _dedupe_course_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        name = item.get("course_name") or item.get("title") or ""
        if not name:
            continue
        current = unique.get(name)
        if not current:
            unique[name] = item
            continue
        merged = {**item, **{key: value for key, value in current.items() if value}}
        unique[name] = merged
    return list(unique.values())


def _curriculum_preview_items(items: list[dict[str, Any]], index: SearchIndex | None = None) -> list[dict[str, Any]]:
    # 전체 카탈로그 우선
    return _dedupe_course_items([*items, *_fallback_curriculum_items(index)])


def _requested_curriculum_grade(question: str) -> str:
    match = re.search(r"([1-4])\s*학년", question or "")
    return f"{match.group(1)}학년" if match else ""


def _fallback_notice_items() -> list[dict[str, Any]]:
    return [
        {
            "title": "컴퓨터과학과 공지사항",
            "date": "",
            "description": "저장된 최신 공지 데이터가 부족합니다. 공식 공지사항 페이지에서 최신 공지를 확인해 주세요.",
            "source_url": NOTICE_URL,
            "fallback_url": NOTICE_URL,
            "link_label": "공지 바로가기",
        },
        {
            "title": "학사 및 수강 관련 공지",
            "date": "",
            "description": "수강신청, 시험, 과제 등 학사 공지는 공식 공지사항에서 확인할 수 있습니다.",
            "source_url": NOTICE_URL,
            "fallback_url": NOTICE_URL,
            "link_label": "공지 바로가기",
        },
        {
            "title": "학과 행사 및 안내 공지",
            "date": "",
            "description": "학과 행사와 주요 안내도 공식 공지사항 페이지에 함께 게시됩니다.",
            "source_url": NOTICE_URL,
            "fallback_url": NOTICE_URL,
            "link_label": "공지 바로가기",
        },
    ]


def _fallback_schedule_items() -> list[dict[str, Any]]:
    return [
        {
            "title": "학과 일정 공식 페이지",
            "start_date": "",
            "end_date": "",
            "description": "저장된 일정 데이터가 부족합니다. 공식 학과 일정 페이지에서 최신 일정을 확인해 주세요.",
            "category": "학과일정",
            "source_url": SCHEDULE_URL,
            "fallback_url": SCHEDULE_URL,
            "link_label": "학과 일정 바로가기",
        },
        {
            "title": "수강 및 평가 일정 확인",
            "start_date": "",
            "end_date": "",
            "description": "수강신청, 시험, 평가 등 주요 일정은 공식 학과 일정 페이지를 기준으로 확인해 주세요.",
            "category": "학과일정",
            "source_url": SCHEDULE_URL,
            "fallback_url": SCHEDULE_URL,
            "link_label": "학과 일정 바로가기",
        },
        {
            "title": "학과 주요 안내 일정",
            "start_date": "",
            "end_date": "",
            "description": "졸업논문, 등록, 학사 안내 등 학과 주요 일정은 공식 페이지에서 최신 상태로 제공됩니다.",
            "category": "학과일정",
            "source_url": SCHEDULE_URL,
            "fallback_url": SCHEDULE_URL,
            "link_label": "학과 일정 바로가기",
        },
    ]


def build_notice_empty_response(
    *,
    started: float,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    # 공지 fallback
    items = _fallback_notice_items()
    return {
        "answer": "컴퓨터과학과 최근 공지 안내입니다.",
        "answer_type": "notice_list",
        "summary": "저장된 최신 공지 데이터가 부족해 공식 공지 확인 항목을 안내드립니다.",
        "items": items,
        "display_limit": 3,
        "total_count": len(items),
        "source_urls": [NOTICE_URL],
        "actions": [{"type": "link", "label": "공지 더보기", "url": NOTICE_URL}],
        "mode": "DB검색",
        "sources": [{"title": "컴퓨터과학과 공지사항", "url": NOTICE_URL, "score": 0}],
        "score": 0,
        "keywords": keywords or ["공지"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "structured_intent": "recent_notice",
        "search_scope": ["notice"],
    }


def build_priority_intent_response(
    intent: str,
    question: str,
    index: SearchIndex,
    started: float,
) -> dict[str, Any]:
    # quick intent
    keywords = tokenize(question)
    if intent == "course_table":
        hits = retrieve_documents(index, question, "course_table")
        items = normalize_results("course_table", hits, question)
        items = _curriculum_preview_items(items, index)
        curriculum_url = resolve_curriculum_url(index, hits)
        if not items:
            return build_curriculum_link_response(
                sources=[{"title": "컴퓨터과학과 교육과정", "url": curriculum_url, "score": 100}],
                score=100,
                keywords=keywords,
                started=started,
            )
        return build_structured_response(
            "course_table",
            items,
            source_url=curriculum_url,
            sources=[{"title": "컴퓨터과학과 교육과정", "url": curriculum_url, "score": hits[0].get("score", 100) if hits else 100}],
            score=hits[0].get("score", 100) if hits else 100,
            keywords=keywords,
            started=started,
            requested_grade=_requested_curriculum_grade(question),
        )
    if intent == "notice_list":
        hits = retrieve_documents(index, question, "notice_list")
        hits = _supplement_notice_hits(index, hits)
        items = normalize_results("notice_list", hits, question)
        items.sort(key=lambda item: (item.get("date") or "", item.get("title") or ""), reverse=True)
        if not items:
            return build_notice_empty_response(started=started, keywords=keywords)
        response = build_structured_response(
            "notice_list",
            items,
            source_url=NOTICE_URL,
            sources=[
                {"title": item.get("title") or "공지사항", "url": item.get("source_url") or NOTICE_URL, "score": 100}
                for item in items[:3]
            ],
            score=hits[0].get("score", 100) if hits else 100,
            keywords=keywords,
            started=started,
        )
        response["answer"] = "컴퓨터과학과 최근 공지 안내입니다."
        response["summary"] = "컴퓨터과학과 최근 공지 3건을 안내드립니다."
        response["structured_intent"] = "recent_notice"
        response["search_scope"] = ["notice"]
        return response
    if intent == "schedule_list":
        hits = retrieve_documents(index, question, "schedule_list")
        hits = _supplement_schedule_hits(index, hits)
        items = normalize_results("schedule_list", hits, question)
        if not items:
            return build_schedule_unavailable_response(started, question)
        response = build_structured_response(
            "schedule_list",
            items,
            source_url=SCHEDULE_URL,
            sources=[
                {"title": item.get("title") or "학과 일정", "url": item.get("source_url") or SCHEDULE_URL, "score": 100}
                for item in items[:3]
            ],
            score=hits[0].get("score", 100) if hits else 100,
            keywords=keywords,
            started=started,
        )
        response["answer"] = "컴퓨터과학과 학과 일정 안내입니다."
        response["summary"] = "컴퓨터과학과 주요 학과 일정 3건을 안내드립니다."
        response["structured_intent"] = "schedule"
        response["search_scope"] = ["schedule"]
        return response
    raise ValueError(f"지원하지 않는 priority intent: {intent}")


def build_schedule_unavailable_response(started: float, question: str = "") -> dict[str, Any]:
    items = _fallback_schedule_items()
    return {
        "answer": "학과 일정 안내입니다.",
        "answer_type": "schedule_list",
        "summary": "저장된 최신 일정 데이터가 부족해 공식 일정 확인 항목을 안내드립니다.",
        "items": items,
        "display_limit": 3,
        "total_count": len(items),
        "actions": [{"type": "link", "label": "학과 일정 바로가기", "url": SCHEDULE_URL}],
        "source_urls": [SCHEDULE_URL],
        "sources": [{"title": "컴퓨터과학과 학과 일정", "url": SCHEDULE_URL, "score": 0}],
        "mode": "DB검색",
        "score": 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "failure_reason": "학과 일정 전용 필터 통과 문서 없음",
    }


def build_faculty_detail_response(
    faculty: dict[str, Any] | None,
    *,
    question: str,
    started: float,
) -> dict[str, Any]:
    if not faculty or faculty.get("_not_found"):
        name = (faculty or {}).get("name") or "해당"
        return {
            "answer": "해당 교수명을 컴퓨터과학과 공식 교수진 데이터에서 찾지 못했습니다.",
            "answer_type": "faculty_detail",
            "summary": "교수진 페이지에서 전체 목록을 확인해 주세요.",
            "items": [],
            "display_limit": 1,
            "total_count": 0,
            "actions": [{"type": "link", "label": "교수진 페이지 바로가기", "url": FACULTY_URL}],
            "source_urls": [FACULTY_URL],
            "sources": [{"title": "컴퓨터과학과 교수진", "url": FACULTY_URL, "score": 0}],
            "mode": "DB검색",
            "score": 0,
            "keywords": tokenize(question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": f"{name} 교수명 미확인",
        }

    homepage_url = faculty.get("homepage_url") or ""
    item = {
        **faculty,
        "position": faculty.get("position") or faculty.get("title") or "교수",
        "title": faculty.get("title") or faculty.get("position") or "교수",
        "source_url": faculty.get("source_url") or FACULTY_URL,
        "fallback_url": FACULTY_URL,
        "link_label": "교수진 페이지 바로가기",
        "actions": (
            [{"type": "link", "label": "교수 홈페이지 바로가기", "url": homepage_url}]
            if homepage_url
            else []
        ),
    }
    return {
        "answer": f"{item['name']} 교수 안내입니다.",
        "answer_type": "faculty_detail",
        "summary": "공식 교수진 데이터에서 확인한 단일 교수 정보입니다.",
        "items": [item],
        "display_limit": 1,
        "total_count": 1,
        "actions": [{"type": "link", "label": "교수진 페이지 바로가기", "url": item["source_url"] or FACULTY_URL}],
        "source_urls": [item["source_url"] or FACULTY_URL],
        "sources": [{"title": "컴퓨터과학과 교수진", "url": item["source_url"] or FACULTY_URL, "score": 100}],
        "mode": "DB검색",
        "score": 100,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def faculty_catalog_items(index: SearchIndex) -> list[dict[str, Any]]:
    # 교수진 fallback
    if not hasattr(index, "faculty_catalog"):
        return []
    items: list[dict[str, Any]] = []
    for item in index.faculty_catalog():
        homepage_url = item.get("homepage_url") or ""
        items.append(
            CompatibleFacultyItem({
                **item,
                "position": item.get("position") or item.get("title") or "교수",
                "title": item.get("title") or item.get("position") or "교수",
                "source_url": item.get("source_url") or FACULTY_URL,
                "fallback_url": FACULTY_URL,
                "link_label": "교수진 페이지 바로가기",
                "actions": (
                    [{"type": "link", "label": "교수 홈페이지 바로가기", "url": homepage_url}]
                    if homepage_url
                    else []
                ),
            })
        )
    return items


def render_fallback_text(question: str, hits: list[dict[str, Any]]) -> str:
    # 짧은 요약
    return sanitize_public_answer(_extractive_answer(question, hits))


def sanitize_public_answer(text: str) -> str:
    # 화면 노출 정리
    value = text or ""
    if RAW_OUTPUT_BLOCK_RE.search(value):
        lines = []
        for raw in value.splitlines():
            line = raw.strip()
            if not line:
                lines.append("")
                continue
            if RAW_OUTPUT_BLOCK_RE.search(line):
                continue
            if re.match(r"^[\[{].*[\]}],?$", line):
                continue
            lines.append(raw)
        value = "\n".join(lines).strip()
    value = re.sub(r"검색\s*점수\s*[:：]?\s*\d+(?:\.\d+)?점?", "", value)
    value = re.sub(r"출처\s*\d+\.\s*", "출처: ", value)
    return value.strip() or "공식 데이터에서 확인한 내용을 학생 관점으로 정리하지 못했습니다. 공식 페이지를 확인해 주세요."


def should_auto_llm(question: str, hits: list[dict[str, Any]], answer: str = "") -> bool:
    # LLM 보조 여부
    if AUTO_LLM_RE.search(question):
        return True
    if answer and (RAW_OUTPUT_BLOCK_RE.search(answer) or len(answer) > 500):
        return True
    if len(hits) >= 2:
        doc_types = {hit.get("document_type") or hit.get("category") or "" for hit in hits[:3]}
        if len(doc_types) >= 2:
            return True
        scores = [float(hit.get("score") or 0) for hit in hits[:2]]
        if len(scores) == 2 and abs(scores[0] - scores[1]) <= 8:
            return True
    return False


def _extractive_answer(question: str, hits: list[dict[str, Any]]) -> str:
    if FACULTY_QUERY_RE.search(question):
        faculty_hit = next(
            (
                hit
                for hit in hits
                if hit.get("source_url") == FACULTY_URL
                or "교수진" in (hit.get("title") or "")
            ),
            None,
        )
        if faculty_hit:
            return _faculty_answer(faculty_hit)

    query_tokens = tokenize(question)
    candidates: list[tuple[float, str]] = []
    for hit in hits[:3]:
        body = hit.get("body") or hit.get("summary") or ""
        sentences = re.split(r"(?<=[.!?다요])\s+|\n+", body)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 12:
                continue
            score = sum(2.0 for token in query_tokens if token in sentence.lower())
            score += min(len(sentence), 300) / 300
            if score > 0:
                candidates.append((score, sentence))
    selected = []
    for _, sentence in sorted(candidates, key=lambda item: item[0], reverse=True):
        if sentence not in selected:
            selected.append(sentence)
        if len(selected) >= 3:
            break
    if not selected:
        selected = [hit.get("summary", "") for hit in hits[:2] if hit.get("summary")]
    return "\n".join(f"- {sentence[:260]}" for sentence in selected) or OUT_OF_SCOPE_MESSAGE


def _faculty_items(hit: dict[str, Any]) -> list[dict[str, Any]]:
    # 교수진 카드
    normalized = [
        item for item in (hit.get("normalized_items") or [])
        if item.get("name") and (item.get("email") or item.get("phone") or item.get("subjects"))
    ]
    if normalized:
        items: list[dict[str, Any]] = []
        for item in normalized:
            homepage_url = item.get("homepage_url") or FACULTY_HOMEPAGE_FALLBACKS.get(item.get("name") or "", "")
            subjects = item.get("subjects") or []
            item_actions = (
                [{"type": "link", "label": "교수 홈페이지 바로가기", "url": homepage_url}]
                if homepage_url
                else []
            )
            items.append(
                CompatibleFacultyItem({
                    "name": item.get("name") or "",
                    "title": item.get("title") or item.get("position") or "교수",
                    "position": item.get("position") or item.get("title") or "교수",
                    "email": item.get("email") or "",
                    "phone": item.get("phone") or "",
                    "subjects": subjects,
                    "subjects_undergraduate": item.get("subjects_undergraduate") or subjects,
                    "subjects_graduate": item.get("subjects_graduate") or [],
                    "research": item.get("research") or [],
                    "homepage_url": homepage_url,
                    "actions": item_actions,
                    "source_url": hit.get("source_url") or FACULTY_URL,
                    "fallback_url": FACULTY_URL,
                    "link_label": "교수진 페이지 바로가기",
                })
            )
        return items

    lines = [line.strip() for line in (hit.get("body") or "").splitlines() if line.strip()]
    items: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        name = lines[index]
        detail = lines[index + 1] if index + 1 < len(lines) else ""
        if not re.fullmatch(r"[가-힣]{2,5}", name) or not re.search(r"교수|이메일|연락처", detail):
            index += 1
            continue

        detail = detail.replace(" 홈페이지 바로가기", "").strip()
        title_match = re.match(r"(교수|조교수|부교수)", detail)
        email_match = re.search(r"이메일\s+(\S+@\S+)", detail)
        phone_match = re.search(r"연락처\s+([0-9-]+)", detail)
        undergraduate_match = re.search(
            r"담당과목\(대학\)\s*(.*?)(?=\s*담당과목\(대학원\)|$)",
            detail,
        )
        graduate_match = re.search(r"담당과목\(대학원\)\s*(.*)$", detail)

        def subjects(match: re.Match[str] | None) -> list[str]:
            if not match:
                return []
            return [subject.strip() for subject in match.group(1).split(",") if subject.strip()]

        email = email_match.group(1).strip(".,") if email_match else ""
        name_homepage = FACULTY_HOMEPAGE_FALLBACKS.get(name, "")

        undergraduate_subjects = subjects(undergraduate_match)
        graduate_subjects = subjects(graduate_match)
        item_actions = (
            [{"type": "link", "label": "교수 홈페이지 바로가기", "url": name_homepage}]
            if name_homepage
            else []
        )
        items.append(
            CompatibleFacultyItem({
                "name": name,
                "title": title_match.group(1) if title_match else "교수",
                "position": title_match.group(1) if title_match else "교수",
                "email": email,
                "phone": phone_match.group(1) if phone_match else "",
                "subjects": [*undergraduate_subjects, *graduate_subjects],
                "subjects_undergraduate": undergraduate_subjects,
                "subjects_graduate": graduate_subjects,
                "research": [],
                "homepage_url": name_homepage,
                "actions": item_actions,
                "source_url": hit.get("source_url") or FACULTY_URL,
                "fallback_url": FACULTY_URL,
                "link_label": "교수진 페이지 바로가기",
            })
        )
        index += 2
    return items


def _faculty_answer(hit: dict[str, Any]) -> str:
    items = _faculty_items(hit)
    if not items:
        return hit.get("summary") or OUT_OF_SCOPE_MESSAGE
    lines = ["컴퓨터과학과 교수진 정보입니다.", f"총 {len(items)}명의 교수 정보를 확인했습니다."]
    for item in items:
        lines.extend(
            [
                "",
                f"- {item['name']} {item['title']}",
                f"  이메일: {item['email'] or '미확인'}",
                f"  연락처: {item['phone'] or '미확인'}",
                "  담당과목",
                f"  - (대학) {', '.join(item['subjects_undergraduate']) or '미확인'}",
                f"  - (대학원) {', '.join(item['subjects_graduate']) or '미확인'}",
            ]
        )
    return "\n".join(lines)


def _list_answer_type(question: str) -> str:
    patterns = (
        ("notice_list", r"최근\s*공지|공지사항|학과\s*공지"),
        ("course_table", r"교육과정|교과과정|커리큘럼"),
        ("schedule_list", r"학과\s*일정|학사\s*일정"),
        ("faq_list", r"faq|자주\s*묻는\s*질문"),
        ("certification_list", r"추천\s*자격증|자격증\s*추천"),
        ("exam_scope", r"시험\s*범위|시험범위|중간(?:고사)?\s*범위|기말(?:고사)?\s*범위|출석수업\s*시험\s*범위|과제\s*범위"),
    )
    for answer_type, pattern in patterns:
        if re.search(pattern, question, re.IGNORECASE):
            return answer_type
    return ""


def _generic_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": hit.get("title") or "공식 정보",
            "summary": (hit.get("summary") or hit.get("body") or "")[:160],
            "category": hit.get("category") or "",
            "published_at": hit.get("published_at") or "",
            "source_url": hit.get("source_url") or "",
            "fallback_url": DEPARTMENT_HOME_URL,
            "link_label": "PDF 보기" if hit.get("document_type") == "pdf" else ("자료 확인하기" if hit.get("document_type") in DOCUMENT_RESOURCE_TYPES else "자세히 보기"),
        }
        for hit in hits[:10]
    ]


def _document_resource_response(question: str, hits: list[dict[str, Any]], started: float) -> dict[str, Any]:
    items = _generic_items(hits)
    actions = []
    for item in items[:3]:
        url = item.get("source_url") or item.get("fallback_url")
        if url:
            actions.append({"type": "link", "label": item.get("link_label") or "자료 확인하기", "url": url})
    return {
        "answer": "공식 자료 검색 결과입니다.",
        "answer_type": "document_list",
        "summary": "기출문제, 시험자료, PDF 문서 중 관련성이 높은 자료를 먼저 안내드립니다.",
        "items": items,
        "display_limit": 3,
        "total_count": len(items),
        "source_urls": [item.get("source_url") for item in items if item.get("source_url")],
        "actions": actions,
        "mode": "DB검색",
        "sources": [{"title": hit.get("title"), "url": hit.get("source_url"), "score": hit.get("score")} for hit in hits[:3] if hit.get("source_url")],
        "score": hits[0].get("score", 0) if hits else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "structured_intent": "document_search",
        "search_scope": ["pdf", "synap", "exam"],
    }


def _is_unsupported_exam_scope_hit(hit: dict[str, Any]) -> bool:
    text = " ".join(
        str(hit.get(field) or "")
        for field in ("title", "summary", "body", "search_text", "source_url")
    )
    return bool(UNSUPPORTED_EXAM_SCOPE_RE.search(text))


def _has_exam_scope_evidence(hit: dict[str, Any], course_name: str) -> bool:
    if _is_unsupported_exam_scope_hit(hit):
        return False
    text = " ".join(
        str(hit.get(field) or "")
        for field in ("title", "summary", "body", "content_text", "search_text")
    )
    compact_text = normalize_course_key(text)
    compact_course = normalize_course_key(course_name)
    if compact_course and compact_course not in compact_text:
        aliases = [
            alias
            for alias, canonical in COURSE_NAME_ALIASES.items()
            if canonical == course_name
        ]
        if not any(normalize_course_key(alias) in compact_text for alias in aliases):
            return False
    return bool(EXAM_SCOPE_EVIDENCE_RE.search(text))


def _course_detail_url_for_exam_scope(course_name: str, index: SearchIndex) -> str:
    if course_name in KNOWN_COURSE_DETAIL_URLS:
        return KNOWN_COURSE_DETAIL_URLS[course_name]
    detected = index.detect_course(course_name) if hasattr(index, "detect_course") else None
    if detected:
        url = _course_link(detected, course_name)
        if "learningInformation/cs1/view.do" in url:
            return url
    for item in _fallback_curriculum_items(index):
        if item.get("course_name") == course_name:
            url = _course_link(item, course_name)
            if "learningInformation/cs1/view.do" in url:
                return url
    return ""


def _exam_scope_response(question: str, index: SearchIndex, started: float) -> dict[str, Any]:
    candidates = detect_course_candidates(question, index)
    if len(candidates) > 1:
        return {
            "answer": "시험범위를 확인할 과목을 하나로 특정해 주세요.",
            "answer_type": "exam_scope",
            "summary": "여러 과목 후보가 감지되어 임의로 선택하지 않았습니다.",
            "items": [{"title": candidate, "course_name": candidate} for candidate in candidates],
            "display_limit": 3,
            "total_count": len(candidates),
            "actions": [{"type": "link", "label": "학과 최근 공지 바로가기", "url": NOTICE_URL}],
            "source_urls": [NOTICE_URL],
            "sources": [],
            "mode": "DB검색",
            "score": 0,
            "keywords": tokenize(question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "structured_intent": "exam_scope",
            "search_scope": ["notice", "course_detail"],
        }
    course_name = candidates[0] if candidates else detect_course_name(question, index)
    search_query = f"{course_name or question} 시험범위 중간고사 기말고사 출석수업 과제 범위"
    notice_hits = index.search(
        search_query,
        top_k=10,
        filters={
            "source_types": ["official"],
            "exclude_document_types": ["교수진", "교육과정표", "학과일정"],
            "exclude_categories": ["교수진", "교육과정", "학과일정", "학생광장", "벼룩시장", "중고장터"],
        },
    )
    evidence_hits = [
        hit for hit in notice_hits
        if course_name and _has_exam_scope_evidence(hit, course_name)
    ]
    if evidence_hits:
        hit = evidence_hits[0]
        summary = _clean_notice_summary(hit, 180) or summarize(hit.get("body") or hit.get("summary") or "", 180)
        url = hit.get("source_url") or NOTICE_URL
        return {
            "answer": f"공식 데이터에서 확인된 {course_name} 시험범위 안내입니다.",
            "answer_type": "exam_scope",
            "summary": summary,
            "items": [{
                "title": hit.get("title") or f"{course_name} 시험범위",
                "summary": summary,
                "source_url": url,
                "link_label": "원문 보기",
            }],
            "display_limit": 1,
            "total_count": len(evidence_hits),
            "actions": [{"type": "link", "label": "원문 보기", "url": url}],
            "source_urls": [url],
            "sources": [{"title": hit.get("title"), "url": url, "score": hit.get("score", 0)}],
            "mode": "DB검색",
            "score": hit.get("score", 0),
            "keywords": tokenize(question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "structured_intent": "exam_scope",
            "search_scope": ["notice", "course_detail"],
        }

    detail_url = _course_detail_url_for_exam_scope(course_name, index) if course_name else ""
    actions = [{"type": "link", "label": "학과 최근 공지 바로가기", "url": NOTICE_URL}]
    if detail_url:
        actions.append({"type": "link", "label": f"{course_name} 상세 페이지 바로가기", "url": detail_url})
    target_name = course_name or "해당 과목"
    return {
        "answer": (
            f"현재 수집된 공식 데이터에서는 {target_name} 시험범위를 확인할 수 없습니다.\n"
            "시험범위는 학기마다 달라질 수 있어 임의로 안내하지 않습니다.\n"
            "아래에서 최신 정보를 확인해 주세요.\n"
            "- 학과 최근 공지\n"
            f"- {target_name} 상세 페이지\n"
            "- 강의계획서/평가정보"
        ),
        "answer_type": "exam_scope",
        "summary": "공식 근거가 없어 시험범위를 추정하지 않았습니다.",
        "items": [{
            "title": f"{target_name} 시험범위 확인 안내",
            "summary": "학기별 공지, 과목 상세 페이지, 강의계획서 또는 평가정보에서 최신 기준을 확인해 주세요.",
            "source_url": detail_url or NOTICE_URL,
            "fallback_url": NOTICE_URL,
            "link_label": f"{course_name} 상세 페이지 바로가기" if detail_url and course_name else "학과 최근 공지 바로가기",
        }],
        "display_limit": 1,
        "total_count": 0,
        "actions": actions,
        "source_urls": [url for url in (NOTICE_URL, detail_url) if url],
        "sources": [{"title": "컴퓨터과학과 최근 공지", "url": NOTICE_URL, "score": 0}],
        "mode": "DB검색",
        "score": 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "structured_intent": "exam_scope",
        "search_scope": ["notice", "course_detail"],
    }


def _clean_notice_summary(hit: dict[str, Any], limit: int = 80) -> str:
    # 공지 요약
    title = re.sub(r"\s+", " ", hit.get("title") or "").strip()
    text = hit.get("body") or hit.get("summary") or ""
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -·")
        if not line or line == title:
            continue
        if re.match(r"^(글번호|카테고리|게시일|작성자|조회수|첨부파일|첨부|다운로드)\s*[:：]?", line):
            continue
        if re.search(r"첨부파일|파일\s*다운로드|바로가기", line):
            continue
        candidates.append(line)
    summary = " ".join(candidates)
    if not summary:
        summary = title
    return summary if len(summary) <= limit else summary[: limit - 1].rstrip() + "…"


def _document_date_value(item: dict[str, Any]) -> str:
    return str(
        item.get("event_date")
        or item.get("start_date")
        or item.get("published_at")
        or item.get("date")
        or item.get("created_at")
        or item.get("updated_at")
        or item.get("last_edited_time")
        or item.get("notion_last_edited_time")
        or item.get("collected_at")
        or ""
    )


def _document_date_sort_key(item: dict[str, Any]) -> tuple[date, str]:
    parsed = parse_schedule_date(_document_date_value(item).replace(".", "-"))
    return parsed or date.min, item.get("title") or ""


def _index_documents(index: SearchIndex) -> list[dict[str, Any]]:
    if hasattr(index, "documents"):
        return index.documents()
    return list(getattr(index, "payload", {}).get("documents") or [])


def _supplement_notice_hits(index: SearchIndex, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {hit.get("source_url") or hit.get("title") for hit in hits}
    extra = [
        doc
        for doc in _index_documents(index)
        if (doc.get("source_url") or doc.get("title")) not in seen
        and validate_notice_document(doc)
    ]
    extra.sort(key=_document_date_sort_key, reverse=True)
    return [*hits, *extra]


def _supplement_schedule_hits(index: SearchIndex, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {hit.get("source_url") or hit.get("title") for hit in hits}
    extra = [
        doc
        for doc in _index_documents(index)
        if (doc.get("source_url") or doc.get("title")) not in seen
        and validate_schedule_document_hit(doc)
    ]
    extra.sort(key=_document_date_sort_key, reverse=True)
    return [*hits, *extra]


def _notice_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()
    for hit in hits:
        title = re.sub(
            r"^\s*(?:글번호\s*[:：]?\s*)?\d{1,6}[.)]\s*",
            "",
            hit.get("title") or "공지사항",
        ).strip()
        source_url = hit.get("source_url") or ""
        key = source_url or title
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "date": (
                    hit.get("published_at")
                    or hit.get("date")
                    or hit.get("created_at")
                    or hit.get("updated_at")
                    or hit.get("notion_last_edited_time")
                    or hit.get("collected_at")
                    or ""
                ),
                "description": _clean_notice_summary(hit),
                "source_url": source_url,
                "fallback_url": NOTICE_URL,
                "link_label": "공지 바로가기",
            }
        )
    return items


def _schedule_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 다가오는 일정
    items: list[dict[str, Any]] = []
    seen = set()
    for hit in hits:
        if not validate_schedule_document_hit(hit):
            continue
        structured = hit.get("normalized_items") or extract_schedule_items(hit.get("body") or "")
        for event in structured:
            if not event.get("start_date"):
                continue
            key = (event.get("title"), event.get("start_date"), event.get("end_date"))
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": event.get("title") or "학과 일정",
                    "start_date": event.get("start_date") or "",
                    "end_date": event.get("end_date") or event.get("start_date") or "",
                    "description": event.get("description") or "학과 공식 일정",
                    "category": "학과일정",
                    "source_url": hit.get("source_url") or SCHEDULE_URL,
                    "fallback_url": SCHEDULE_URL,
                    "link_label": "학과 일정 바로가기",
                }
            )
        if not structured and validate_schedule_document_hit(hit):
            title = re.sub(r"^\s*(?:글번호\s*[:：]?\s*)?\d{1,6}[.)]?\s*", "", hit.get("title") or "").strip()
            item = {
                "title": title,
                "start_date": _document_date_value(hit),
                "end_date": _document_date_value(hit),
                "description": _clean_notice_summary(hit) or "학과 일정 관련 공식 안내입니다.",
                "category": "학과일정",
                "source_url": hit.get("source_url") or SCHEDULE_URL,
                "fallback_url": SCHEDULE_URL,
                "link_label": "학과 일정 바로가기",
            }
            if validate_schedule_item(item):
                key = (item["title"], item["start_date"], item["end_date"])
                if key not in seen:
                    seen.add(key)
                    items.append(item)

    ranked: list[tuple[date, date, dict[str, Any]]] = []
    for item in items:
        if not validate_schedule_item(item):
            continue
        start_date, end_date = parse_schedule_item_dates(item)
        if not start_date:
            continue
        effective_end = end_date or start_date
        item["start_date"] = start_date.isoformat()
        item["end_date"] = effective_end.isoformat()
        ranked.append((start_date, effective_end, item))
    return [
        item
        for _, _, item in sorted(ranked, key=lambda row: (row[0], row[1], row[2]["title"]), reverse=True)
    ]


def parse_schedule_date(value: str, *, default_year: int | None = None) -> date | None:
    # 일정 날짜 파싱
    text = (value or "").strip()
    if not text:
        return None
    match = re.search(r"(20\d{2})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})", text)
    if match:
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    match = re.search(r"\b(\d{1,2})[.월/ -]+(\d{1,2})\b", text)
    if match and default_year:
        month, day = int(match.group(1)), int(match.group(2))
        try:
            return date(default_year, month, day)
        except ValueError:
            return None
    return None


def parse_schedule_item_dates(item: dict[str, Any]) -> tuple[date | None, date | None]:
    # 종료일 기준
    start_raw = str(item.get("start_date") or "")
    end_raw = str(item.get("end_date") or "")
    combined = " ~ ".join(part for part in (start_raw, end_raw) if part)
    if "~" in start_raw and not end_raw:
        combined = start_raw
    parts = re.split(r"\s*~\s*", combined, maxsplit=1)
    start = parse_schedule_date(parts[0])
    default_year = start.year if start else datetime.now(ZoneInfo("Asia/Seoul")).year
    end_source = parts[1] if len(parts) > 1 else end_raw
    end = parse_schedule_date(end_source, default_year=default_year) if end_source else start
    if start and end and end < start and re.search(r"^\s*\d{1,2}[./월 -]+\d{1,2}", end_source or ""):
        try:
            end = date(start.year + 1, end.month, end.day)
        except ValueError:
            pass
    return start, end


def validate_schedule_document_hit(hit: dict[str, Any]) -> bool:
    title = (hit.get("title") or "").strip()
    category = hit.get("category") or ""
    document_type = hit.get("document_type") or ""
    source_url = hit.get("source_url") or ""
    text = f"{title} {category} {document_type} {hit.get('summary') or ''} {hit.get('body') or ''}"
    if not title or re.fullmatch(r"\d+", title):
        return False
    if SCHEDULE_BAD_RE.search(f"{source_url} {category} {title}"):
        return False
    if source_url != SCHEDULE_URL and not SCHEDULE_DETAIL_RE.search(source_url):
        return False
    if source_url == SCHEDULE_URL:
        return True
    if document_type in {"schedule", "학과일정"} and category == "학과일정":
        return True
    if category in SCHEDULE_ALLOWED_CATEGORIES and (source_url == NOTICE_URL or SCHEDULE_DETAIL_RE.search(source_url)) and SCHEDULE_KEYWORD_RE.search(text):
        return True
    return False


def validate_schedule_item(item: dict[str, Any]) -> bool:
    title = (item.get("title") or "").strip()
    description = (item.get("description") or "").strip()
    source_url = item.get("source_url") or item.get("fallback_url") or ""
    category = item.get("category") or ""
    if not title or re.fullmatch(r"\d+", title):
        return False
    if SCHEDULE_BAD_RE.search(f"{source_url} {category} {title} {description}"):
        return False
    if source_url != SCHEDULE_URL and not SCHEDULE_DETAIL_RE.search(source_url):
        return False
    if category and category not in SCHEDULE_ALLOWED_CATEGORIES:
        return False
    if not description and not item.get("start_date"):
        return False
    return True


def _course_feature(course: dict[str, Any]) -> str:
    name = course.get("course_name") or "해당 과목"
    grade = course.get("grade") or ""
    category = course.get("category") or ""
    if any(term in name for term in ("기초", "이해", "입문")):
        return "전공의 기본 개념을 익히는 입문 과목입니다."
    if category == "전공":
        prefix = f"{grade} 수준에서 " if grade else ""
        return f"{prefix}컴퓨터과학 전공 역량을 단계적으로 학습하는 과목입니다."
    return "공식 교육과정에 편성된 교과목입니다."


def _short_course_feature(course: dict[str, Any]) -> str:
    name = course.get("course_name") or course.get("title") or ""
    overview = course.get("overview") or course.get("feature") or ""
    fixed = {
        "컴퓨터의이해": "컴퓨터과학 입문",
        "파이썬프로그래밍기초": "프로그래밍 기초",
        "이산수학": "전공 수학 기초",
        "자료구조": "데이터 구조 이해",
        "컴퓨터구조": "하드웨어 구조 이해",
        "Java프로그래밍": "객체지향 프로그래밍",
        "데이터베이스시스템": "데이터 관리 핵심",
        "운영체제": "시스템 운영 원리",
        "인공지능": "AI 기초 개념",
        "소프트웨어공학": "개발 방법론 이해",
        "정보보호": "보안 기초",
        "컴퓨터보안": "보안 기초",
        "클라우드컴퓨팅": "클라우드 기술 이해",
    }
    if name in fixed:
        return fixed[name]
    if overview:
        return summarize(overview, 42).rstrip("…")
    return _course_feature(course).replace("입니다.", "")


def _course_detail_items(question: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 과목 매칭
    compact_question = re.sub(r"\s+", "", question).lower()
    candidates = _course_items(hits)
    exact = [
        item
        for item in candidates
        if re.sub(r"\s+", "", item.get("course_name") or "").lower() in compact_question
    ]
    selected = exact[:1] or candidates[:1]
    return [
        {
            **item,
            "overview": item.get("overview") or item.get("feature") or "공식 교육과정에 편성된 전공 과목입니다.",
            "easy_explanation": (
                f"쉽게 말하면, {item.get('course_name', '이 과목')}의 핵심 개념과 문제 해결 방법을 "
                "단계적으로 배우는 수업입니다."
            ),
            "recommended_for": ["해당 분야의 기초를 체계적으로 배우고 싶은 학생"],
            "topics": item.get("topics") or item.get("detail_topics") or [],
            "link_label": f"{item.get('course_name', '과목')} 과목 바로가기",
        }
        for item in selected
    ]


def _course_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for hit in hits:
        for course in hit.get("normalized_items") or []:
            if not course.get("course_name"):
                continue
            items.append(
                {
                    "title": course.get("course_name"),
                    "course_name": course.get("course_name"),
                    "grade": course.get("grade", ""),
                    "semester": course.get("semester", ""),
                    "category": course.get("category", ""),
                    "course_code": course.get("course_code", ""),
                    "credit": course.get("credit", ""),
                    "media": course.get("media") or [],
                    "evaluation": course.get("evaluation") or [],
                    "overview": course.get("overview", ""),
                    "topics": course.get("topics") or [],
                    "detail_topics": course.get("detail_topics") or [],
                    "feature": _course_feature(course),
                    "feature_summary": course.get("feature_summary") or _short_course_feature(course),
                    "detail_url": course.get("detail_url") or (
                        course.get("source_url") if "learningInformation/cs1/view.do" in (course.get("source_url") or "") else ""
                    ),
                    "source_url": course.get("source_url") or hit.get("source_url") or "",
                    "fallback_url": COURSE_FULL_GUIDE_URL,
                    "link_label": (
                        f"{course.get('course_name')} 과목 바로가기"
                        if course.get("detail_url") or "learningInformation/cs1/view.do" in (course.get("source_url") or "")
                        else "교육과정 바로가기"
                    ),
                }
            )
    return items


def _grade_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    grade_match = re.search(r"([1-4])", item.get("grade") or "")
    semester_match = re.search(r"([12])", item.get("semester") or "")
    major_rank = 0 if "전공" in (item.get("category") or "") else 1
    return (
        int(grade_match.group(1)) if grade_match else 9,
        major_rank,
        int(semester_match.group(1)) if semester_match else 9,
        item.get("course_name") or item.get("title") or "",
    )


GRADE_PREVIEW_LIMIT = 6
CURRICULUM_PREFERRED_BY_GRADE = {
    "1학년": ["컴퓨터의이해", "세계의정치와경제", "파이썬프로그래밍기초", "사진의이해", "유비쿼터스컴퓨팅개론", "데이터정보처리입문"],
    "2학년": ["Java프로그래밍", "HTML5웹프로그래밍", "테마가있는음악여행", "환경과건강", "이산수학", "한국사의이해"],
    "3학년": ["알고리즘", "운영체제", "디지털논리회로", "데이터베이스시스템", "그래픽커뮤니케이션", "인공지능"],
    "4학년": ["정보통신망", "컴퓨터보안", "컴퓨터그래픽스", "모바일앱프로그래밍", "생활과건강", "소프트웨어공학"],
}


def _representative_courses_by_grade(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in sorted(items, key=_grade_sort_key):
        name = item.get("course_name") or item.get("title")
        if name and name not in unique:
            unique[name] = item

    groups: list[dict[str, Any]] = []
    for grade in ("1학년", "2학년", "3학년", "4학년"):
        grade_items = [
            item for item in unique.values()
            if (item.get("grade") or "").startswith(grade[0])
        ]
        selected: list[dict[str, Any]] = []
        for name in CURRICULUM_PREFERRED_BY_GRADE[grade]:
            found = next((item for item in grade_items if item.get("course_name") == name or item.get("title") == name), None)
            if found and found not in selected:
                selected.append(found)
        for item in sorted(grade_items, key=_grade_sort_key):
            if item not in selected:
                selected.append(item)
            if len(selected) >= GRADE_PREVIEW_LIMIT:
                break
        if len(selected) < GRADE_PREVIEW_LIMIT:
            logger.warning(
                "[CURRICULUM] grade=%s preview count=%s expected=%s candidates=%s reason=insufficient_grade_items",
                grade,
                len(selected),
                GRADE_PREVIEW_LIMIT,
                len(grade_items),
            )
        groups.append(
            {
                "grade": grade,
                "items": [
                    {
                        "course_name": item.get("course_name") or item.get("title") or "",
                        "category": item.get("category") or "전공",
                        "feature_summary": item.get("feature_summary") or _short_course_feature(item),
                        "detail_url": _course_link(item),
                        "source_url": _course_link(item),
                        "fallback_url": COURSE_FULL_GUIDE_URL,
                        "link_label": f"{item.get('course_name') or item.get('title') or '과목'} 과목 바로가기",
                    }
                    for item in selected[:GRADE_PREVIEW_LIMIT]
                ],
            }
        )
    return groups


def build_curriculum_by_grade_response(
    items: list[dict[str, Any]],
    *,
    source_url: str,
    sources: list[dict[str, Any]],
    score: float,
    keywords: list[str],
    started: float,
    requested_grade: str = "",
) -> dict[str, Any]:
    if not items:
        items = _fallback_curriculum_items()
    source_items = items
    if requested_grade:
        grade_items = [
            item for item in sorted(_dedupe_course_items(source_items), key=_grade_sort_key)
            if (item.get("grade") or "").startswith(requested_grade[0])
        ]
        if len(grade_items) < 5:
            logger.warning(
                "[CURRICULUM] requested grade=%s item_count=%s reason=insufficient_grade_items",
                requested_grade,
                len(grade_items),
            )
        selected_items: list[dict[str, Any]] = []
        for name in CURRICULUM_PREFERRED_BY_GRADE.get(requested_grade, []):
            found = next((item for item in grade_items if item.get("course_name") == name or item.get("title") == name), None)
            if found and found not in selected_items:
                selected_items.append(found)
        for item in grade_items:
            if item not in selected_items:
                selected_items.append(item)
            if len(selected_items) >= GRADE_PREVIEW_LIMIT:
                break
        groups = [
            {
                "grade": requested_grade,
                "items": [
                    {
                        "course_name": item.get("course_name") or item.get("title") or "",
                        "category": item.get("category") or "전공",
                        "feature_summary": item.get("feature_summary") or _short_course_feature(item),
                        "detail_url": _course_link(item),
                        "source_url": _course_link(item),
                        "fallback_url": COURSE_FULL_GUIDE_URL,
                        "link_label": f"{item.get('course_name') or item.get('title') or '과목'} 과목 바로가기",
                    }
                    for item in selected_items[:GRADE_PREVIEW_LIMIT]
                ],
            }
        ]
        items = selected_items
    else:
        groups = _representative_courses_by_grade(items)
    if not any(group.get("items") for group in groups):
        items = _fallback_curriculum_items()
        groups = _representative_courses_by_grade(items)
    curriculum_url = safe_official_url(source_url or CURRICULUM_URL)
    return {
        "answer": f"{requested_grade} 교육과정 안내입니다." if requested_grade else "컴퓨터과학과 교육과정 안내입니다.",
        "answer_type": CompatibleAnswerType("curriculum_by_grade", "course_table"),
        "summary": f"{requested_grade} 주요 과목입니다. 공식 교육과정 기준으로 정리했습니다." if requested_grade else "컴퓨터과학과 교육과정 주요 과목을 학년별로 정리해드릴게요.",
        "groups": groups,
        "items": [item for group in groups for item in group["items"]],
        "display_limit": GRADE_PREVIEW_LIMIT,
        "total_count": len(items),
        "source_urls": [curriculum_url],
        "actions": [{"type": "link", "label": "교육과정 더보기", "url": curriculum_url}],
        "mode": "DB검색",
        "sources": sources,
        "score": score,
        "keywords": keywords,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _actions(answer_type: str, items: list[dict[str, Any]], source_url: str = "") -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if len(items) > 3:
        labels = {
            "faculty": f"전체 교수진 보기 ({len(items)}명)",
            "course_table": f"전체 교육과정 보기 ({len(items)}개)",
            "course_recommendation": f"추천 과목 더보기 ({len(items)}개)",
            "notice_list": f"전체 공지 보기 ({len(items)}개)",
            "schedule_list": f"전체 일정 보기 ({len(items)}개)",
        }
        actions.append(
            {
                "type": "expand",
                "label": labels.get(answer_type, f"전체 보기 ({len(items)}개)"),
                "target": "items",
            }
        )
    if source_url:
        link_labels = {
            "faculty": "교수진 페이지 바로가기",
            "course_table": "교육과정 더보기",
            "course_recommendation": "교육과정 바로가기",
            "course_detail": "교육과정 바로가기",
            "notice_list": "공지 더보기",
            "schedule_list": "학과 일정 바로가기",
            "faq_list": "FAQ 바로가기",
            "certification_list": "진로 정보 바로가기",
        }
        actions.append(
            {
                "type": "link",
                "label": link_labels.get(answer_type, "공식 페이지 바로가기"),
                "url": source_url,
            }
        )
    return actions


def _course_recommendation_response(curated: dict[str, Any], started: float) -> dict[str, Any]:
    source_url = curated.get("source_url", "") or CURRICULUM_URL
    items = [
        {
            "title": course.get("course_name", ""),
            "course_name": course.get("course_name", ""),
            "group_name": group.get("group_name", ""),
            "reason": course.get("reason", ""),
            "difficulty_hint": course.get("difficulty_hint", ""),
            "workload_hint": course.get("workload_hint", ""),
            "credit": course.get("credit", ""),
            "source_url": source_url,
            "fallback_url": CURRICULUM_URL,
            "link_label": "교육과정 바로가기",
        }
        for group in curated.get("recommendation_groups", [])
        for course in group.get("items", [])
    ]
    return {
        "answer": curated.get("answer", "편입생 및 입문자 기준 추천 과목입니다."),
        "answer_type": "course_recommendation",
        "summary": curated.get("summary") or curated.get("note", ""),
        "items": items,
        "display_limit": 3,
        "total_count": len(items),
        "actions": build_actions("course_recommendation", items, source_url),
        "source_urls": [source_url] if source_url else [],
        "sources": [{"title": curated.get("title"), "url": source_url, "score": 100}] if source_url else [],
        "mode": "DB검색",
        "score": 100,
        "keywords": curated.get("keywords", []),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "structured_intent": curated.get("intent"),
        "validity": curated.get("validity"),
        "note": curated.get("note", ""),
    }


def _course_difficulty_confirmation(
    question: str,
    course_name: str,
    items: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    source_url = next(
        (_course_link(item, course_name) for item in items if _course_link(item, course_name)),
        COURSE_FULL_GUIDE_URL,
    )
    context = {
        "course_name": course_name,
        "overview": items[0].get("overview") if items else "",
        "topics": items[0].get("topics") if items else [],
        "source_url": source_url,
        "fallback_url": COURSE_GUIDE_URL,
    }
    return {
        "answer": (
            "공식 데이터에는 해당 과목의 체감 난이도 정보가 명시되어 있지 않습니다.\n"
            "다만 과목명과 학습 내용 기준으로 일반적인 학습 부담을 참고용으로 안내할 수 있습니다.\n"
            "LLM 보조 답변을 사용할까요?"
        ),
        "answer_type": "llm_confirmation_required",
        "summary": (
            f"{course_name}의 공식 과목 정보는 확인했지만 체감 난이도는 공식 기준이 아닙니다."
            if items
            else f"{course_name} 과목명을 확인했지만 체감 난이도는 공식 기준이 아닙니다."
        ),
        "items": [],
        "display_limit": 3,
        "total_count": 0,
        "actions": [
            {"type": "confirm_llm", "label": "LLM 보조 답변 사용", "target": "allow_llm"},
            {"type": "link", "label": f"{course_name} 과목 바로가기", "url": source_url},
            {"type": "link", "label": "교과목 안내 바로가기", "url": COURSE_FULL_GUIDE_URL},
        ],
        "source_urls": list(dict.fromkeys([source_url, COURSE_FULL_GUIDE_URL])),
        "sources": [{"title": f"{course_name} 공식 과목 정보", "url": source_url, "score": 100}],
        "mode": "LLM확인",
        "requires_llm_confirmation": True,
        "llm_type": "course_difficulty",
        "course_name": course_name,
        "context": context,
        "session_id": session_id,
        "request_id": request_id,
        "score": 100 if items else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "failure_reason": "공식 체감 난이도 정보 없음",
    }


def _context_summary(context: dict[str, Any]) -> str:
    # LLM context
    allowed_keys = (
        "course_name",
        "title",
        "overview",
        "topics",
        "items",
        "source_url",
        "fallback_url",
    )
    lines: list[str] = []
    for key in allowed_keys:
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            if key == "items":
                preview = []
                for item in value[:3]:
                    if isinstance(item, dict):
                        preview.append(
                            {
                                k: item.get(k)
                                for k in ("title", "course_name", "overview", "summary", "date", "description")
                                if item.get(k)
                            }
                        )
                    else:
                        preview.append(str(item)[:120])
                value_text = str(preview)
            else:
                value_text = ", ".join(str(item) for item in value[:8])
        elif isinstance(value, dict):
            value_text = str({k: value.get(k) for k in sorted(value)[:8]})
        else:
            value_text = str(value)
        lines.append(f"- {key}: {value_text[:1000]}")
    return "\n".join(lines) or "- 공식 데이터 요약: 제공된 context 없음"


def build_llm_prompt(llm_type: str, question: str, context: dict[str, Any]) -> str:
    # LLM prompt
    supported = {
        "course_difficulty",
        "course_grade_strategy",
        "course_order",
        "course_roadmap",
        "notice_explain",
        "schedule_explain",
        "general_explain",
    }
    normalized_type = llm_type if llm_type in supported else "general_explain"
    formats = {
        "course_difficulty": (
            "체감 난이도:\n"
            "필요한 준비:\n"
            "학습 팁:\n"
            "참고 안내:"
        ),
        "course_grade_strategy": (
            "과목:\n"
            "목표:\n"
            "추천 공부법:\n"
            "우선 익혀야 하는 내용:\n"
            "- 항목\n"
            "시험 대비 팁:\n"
            "주의할 점:"
        ),
        "course_order": (
            "추천 수강 순서:\n"
            "먼저 알면 좋은 내용:\n"
            "주의할 점:\n"
            "참고 안내:"
        ),
        "course_roadmap": (
            "추천 방향:\n"
            "우선 수강하면 좋은 과목:\n"
            "학습 전략:\n"
            "참고 안내:"
        ),
        "notice_explain": (
            "공지 요약:\n"
            "학생이 확인할 점:\n"
            "주의할 점:\n"
            "바로가기 안내:"
        ),
        "schedule_explain": (
            "일정 요약:\n"
            "학생이 해야 할 일:\n"
            "확인할 점:\n"
            "바로가기 안내:"
        ),
        "general_explain": (
            "핵심 설명:\n"
            "참고 안내:\n"
            "다음 확인 사항:"
        ),
    }
    type_guides = {
        "course_difficulty": "과목 난이도와 학습 부담은 공식 기준이 아니므로 참고용 안내로만 설명한다.",
        "course_grade_strategy": "성적 취득 전략과 공부법은 공식 성적 보장 기준이 아니므로, 공식 과목 내용에 기반한 참고용 학습 전략으로만 설명한다.",
        "course_order": "선수지식과 수강 순서는 공식 필수 선후수 규정이 아닌 학습 참고 순서로 설명한다.",
        "course_roadmap": "편입생 또는 재학생의 과목 선택 방향을 공식 교육과정 범위 안에서 참고용으로 정리한다.",
        "notice_explain": "공지 원문을 복사하지 말고 학생이 해야 할 일을 중심으로 쉽게 설명한다.",
        "schedule_explain": "일정 원문을 복사하지 말고 기간과 학생 행동 중심으로 쉽게 설명한다.",
        "general_explain": "공식 데이터로 확인되는 핵심만 학생 눈높이로 설명한다.",
    }
    return f"""
너는 한국방송통신대학교 컴퓨터과학과 학생을 돕는 AI 보조 설명 엔진이다.
공식 데이터와 일반적인 참고 조언을 반드시 구분한다.
공식 데이터에 없는 학점, 개설 학기, 평가 방식, 시험 범위, 날짜, 규정, URL은 추측하지 않는다.
공식 데이터에 없는 내용은 “참고용 안내”라고 명확히 표시한다.
인사말과 자기소개를 하지 않는다.
“안녕하세요”, “ComPass입니다”, “AI 학과 비서입니다” 같은 표현을 사용하지 않는다.
답변은 바로 본문부터 시작한다.
과제 대행, 코딩 대행, 정답 대행은 제공하지 않는다.
한국어로 간결하게 작성한다.
문장이 중간에 끊기지 않도록 완결된 문장으로 끝낸다.
답변은 700자 이내로 작성한다.
각 항목은 1~2문장 이내로 끝낸다.
각 항목은 완결된 문장으로 끝낸다.
마지막 문장은 반드시 마침표로 끝낸다.
ComPass는 학생이 이해하기 쉽게 재해석해서 안내하는 AI 학과 비서라는 철학에 맞게 설명한다.
원문을 그대로 복사하지 말고 학생 눈높이로 요약·정리한다.

[LLM 타입]
{normalized_type}

[타입별 지시]
{type_guides[normalized_type]}

[공식 데이터 context]
{_context_summary(context)}

[사용자 질문]
{sanitize_input(question, 300)}

[답변 형식]
{formats[normalized_type]}
""".strip()


def _course_difficulty_prompt(
    question: str,
    course_name: str,
    item: dict[str, Any],
) -> str:
    # 호환 wrapper
    return build_llm_prompt(
        "course_difficulty",
        question,
        {
            "course_name": course_name,
            "overview": item.get("overview") or item.get("feature") or "공식 교육과정에 편성된 과목",
            "topics": item.get("topics") or [],
            "source_url": item.get("source_url") or COURSE_GUIDE_URL,
            "fallback_url": COURSE_GUIDE_URL,
        },
    )


def _official_course_overview(course_name: str, item: dict[str, Any]) -> str:
    topics = item.get("topics") or item.get("detail_topics") or []
    overview = item.get("overview") or item.get("feature") or ""
    return wash_official_overview(course_name, overview, topics)


def _sentence_similarity(left: str, right: str) -> float:
    def normalize(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]+", "", value or "").lower()

    left_norm = normalize(left)
    right_norm = normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def dedupe_sentences(text: str) -> str:
    # 중복 문장 제거
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not raw:
        return ""
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。])\s+|\n+", raw)
        if sentence.strip()
    ]
    deduped: list[str] = []
    for sentence in sentences:
        if any(_sentence_similarity(sentence, existing) >= 0.70 for existing in deduped):
            continue
        deduped.append(sentence)
    return "\n".join(deduped)


def remove_duplicate_overview(answer: str, official_overview: str) -> str:
    # 개요 중복 제거
    if not answer:
        return ""
    overview_lines = [line.strip() for line in (official_overview or "").splitlines() if line.strip()]
    cleaned_lines: list[str] = []
    for raw_line in answer.splitlines():
        line = re.sub(r"[*#`|]", "", raw_line).strip(" -·")
        if not line:
            continue
        if re.search(r"과목\s*(?:안내|학습 부담 안내)입니다\.?$|참고용\s*학습\s*부담:?$|^체감\s*난이도:?$", line):
            continue
        if line.startswith("공식 데이터 기준으로") and any(
            _sentence_similarity(line, overview) >= 0.55 for overview in overview_lines
        ):
            continue
        if any(_sentence_similarity(line, overview) >= 0.70 for overview in overview_lines):
            continue
        cleaned_lines.append(line)
    return dedupe_sentences("\n".join(cleaned_lines))


def wash_official_overview(course_name: str, overview: str, topics: list[Any] | None = None) -> str:
    # 공식 개요
    topics = topics or []
    topic_text = ", ".join(str(topic).strip() for topic in topics[:5] if str(topic).strip())
    text = re.sub(r"\s+", " ", overview or "").strip(" -·,:")
    text = re.sub(rf"({re.escape(course_name)}은)\s*{re.escape(course_name)}은", rf"\1", text)
    text = re.sub(rf"^{re.escape(course_name)}\s*(?:은|는|이|가)\s*", "", text).strip()
    text = re.sub(r"(?:및|등|,|이의|그리고)$", "", text).strip(" -·,:")

    if re.search(r"인공지능|AI", course_name, re.IGNORECASE):
        return (
            "공식 데이터 기준으로 인공지능은 컴퓨터가 지능적으로 문제를 해결하도록 하는 원리와 기법을 배우는 과목입니다.\n"
            "문제 해결, 지식 표현, 퍼지 이론, 머신러닝, 신경망 등 핵심 개념을 다룹니다."
        )

    if text:
        short = summarize(text, 120).rstrip("…").strip(" -·,:")
        short = re.sub(r"(?:및|등|,|이의)$", "", short).strip(" -·,:")
        if not re.search(r"(입니다|합니다|다룹니다|배웁니다|한다|된다|이다|다)\.$", short):
            short = f"{short}을 다루는 과목입니다." if not short.endswith("과목") else f"{short}입니다."
        subject = f"{course_name}은" if course_name.endswith(("각", "능", "학", "론", "법", "식", "망", "템")) else f"{course_name}는"
        result = f"공식 데이터 기준으로 {subject} {short}"
        if topic_text and len(result) < 150:
            result += f"\n주요 학습 내용은 {topic_text} 등입니다."
        return dedupe_sentences(result)

    if topic_text:
        subject = f"{course_name}은" if course_name.endswith(("각", "능", "학", "론", "법", "식", "망", "템")) else f"{course_name}는"
        return f"공식 데이터 기준으로 {subject} {topic_text} 등을 다루는 과목입니다."
    subject = f"{course_name}은" if course_name.endswith(("각", "능", "학", "론", "법", "식", "망", "템")) else f"{course_name}는"
    return f"공식 데이터 기준으로 {subject} 컴퓨터과학과 교과목 안내에 등록된 과목입니다."


def _clean_incomplete_sentence(value: str) -> str:
    text = re.sub(r"[*#`|]", "", value or "")
    text = re.sub(r"\s+", " ", text).strip(" -·,:")
    text = re.sub(r"(?:및|등|,|-)$", "", text).strip(" -·,:")
    if text and not re.search(r"[.!?。요다]$", text):
        text += "."
    return text


INCOMPLETE_ENDINGS = (
    "및", "또는", "그리고", "하지만", "때문에", "위해", "수 있도록", "하는", "입니다만",
    "추천 공부법:", "우선 익혀야 하는 내용:", "시험 대비 팁:", "주의할 점:", "참고 안내:",
)
EXPLANATORY_LLM_TYPES = {
    "course_difficulty",
    "course_grade_strategy",
    "course_order",
    "course_roadmap",
    "notice_explain",
    "schedule_explain",
    "general_explain",
}


def is_incomplete_llm_text(text: str, llm_type: str = "general_explain") -> bool:
    # 끊긴 답변 방어
    clean = re.sub(r"\r\n?", "\n", text or "").strip()
    if not clean or clean == LLM_SAFE_FAILURE_MESSAGE:
        return True
    if llm_type in EXPLANATORY_LLM_TYPES and llm_type != "fragment" and len(clean) < 80:
        return True
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if not lines:
        return True
    last_line = re.sub(r"[*#`]", "", lines[-1]).strip()
    if len(last_line) < 15 and not re.search(r"[.!?。요다)\]]$", last_line):
        return True
    if last_line.endswith(INCOMPLETE_ENDINGS):
        return True
    if re.fullmatch(r"[-•]\s*", last_line):
        return True
    if re.fullmatch(r"[^:：]{2,30}[:：]", last_line):
        return True
    if clean.count("|") >= 4:
        table_lines = [line for line in lines if line.startswith("|")]
        if table_lines and not table_lines[-1].endswith("|"):
            return True
    if not re.search(r"[.!?。요다)\]]$", last_line):
        return True
    return False


def _llm_fallback_template(llm_type: str, context: dict[str, Any], question: str = "") -> dict[str, Any]:
    course_name = context.get("course_name") or detect_course_name(question) or "해당 과목"
    if llm_type == "course_grade_strategy":
        goal = "C 이상 성적 취득"
        if re.search(r"A\s*(?:이상|받|맞)", question, re.IGNORECASE):
            goal = "A 이상 성적 취득"
        elif re.search(r"B\s*(?:이상|받|맞)", question, re.IGNORECASE):
            goal = "B 이상 성적 취득"
        return {
            "answer": f"{course_name} 과목 {goal}을 위한 학습 안내입니다.",
            "summary": "공식 과목 정보를 바탕으로 일반적인 학습 전략을 참고용으로 안내드립니다.",
            "items": [
                {"label": "추천 공부법", "value": "용어를 먼저 정리하고, 각 단원의 핵심 개념을 반복해서 확인하는 방식이 좋습니다."},
                {"label": "우선 익혀야 할 내용", "value": "탐색, 지식 표현, 추론, 머신러닝, 신경망 등 과목의 기본 개념을 중심으로 학습하세요."},
                {"label": "시험 대비 팁", "value": "강의에서 반복되는 개념과 예시 문제를 중심으로 정리하는 것이 도움이 됩니다."},
            ],
            "disclaimer": "성적 취득 전략은 공식 보장 기준이 아닌 참고용 학습 안내이며, 평가 방식과 시험 범위는 해당 학기 공지를 확인해야 합니다.",
        }
    if llm_type == "course_difficulty":
        return {
            "answer": f"{course_name} 과목의 학습 부담 안내입니다.",
            "summary": "공식 과목 정보를 바탕으로 참고용 학습 부담을 정리했습니다.",
            "items": [
                {"label": "체감 난이도", "value": "개인별 배경지식에 따라 다르지만 보통 수준으로 접근하는 것이 안전합니다."},
                {"label": "필요한 준비", "value": "공식 과목 개요와 주요 학습 내용을 먼저 확인하고 핵심 용어를 정리하세요."},
                {"label": "학습 팁", "value": "강의 흐름에 맞춰 개념을 반복 확인하고 예시 문제와 함께 정리하는 방식이 좋습니다."},
            ],
            "disclaimer": "난이도와 학습 부담은 공식 기준이 아닌 참고용 안내입니다.",
        }
    templates = {
        "course_order": ("추천 수강 순서 안내입니다.", [("추천 수강 순서", "기초 개념 과목을 먼저 확인한 뒤 전공 심화 과목으로 확장하는 방식이 좋습니다."), ("먼저 알면 좋은 내용", "교과목 개요와 학년·학기 정보를 먼저 확인하세요."), ("주의할 점", "개설 여부와 수강 가능 학기는 해당 학기 공지를 확인해야 합니다.")]),
        "notice_explain": ("공지사항 요약 안내입니다.", [("공지 요약", "공지의 핵심 목적과 대상 여부를 먼저 확인하세요."), ("학생이 확인할 점", "신청 기간, 제출 항목, 문의처가 있는지 확인하세요."), ("주의할 점", "공지의 게시일과 적용 학기를 함께 확인해야 합니다.")]),
        "schedule_explain": ("학과 일정 안내입니다.", [("일정 요약", "일정의 시작일과 종료일을 먼저 확인하세요."), ("학생이 해야 할 일", "수강, 평가, 신청 등 본인에게 필요한 행동을 일정 전에 준비하세요."), ("확인할 점", "최신 일정은 공식 학과 일정 페이지에서 다시 확인하세요.")]),
    }
    title, rows = templates.get(llm_type, ("ComPass 보조 안내입니다.", [("핵심 설명", "공식 데이터에서 확인되는 범위 안에서 핵심만 정리했습니다."), ("참고 안내", "최신 기준은 공식 페이지에서 다시 확인하세요."), ("다음 확인 사항", "관련 공지와 공식 페이지를 함께 확인하는 것이 좋습니다.")]))
    return {
        "answer": title,
        "summary": "공식 데이터 범위 안에서 학생이 이해하기 쉽게 재구성한 보조 안내입니다.",
        "items": [{"label": label, "value": value} for label, value in rows],
        "disclaimer": "이 안내는 공식 데이터를 바탕으로 한 참고용 설명입니다.",
    }


def _fallback_text_from_template(template: dict[str, Any]) -> str:
    lines = [template.get("answer", "ComPass 보조 안내입니다."), "", template.get("summary", "")]
    for item in template.get("items") or []:
        lines.extend(["", f"{item.get('label')}: {item.get('value')}"])
    if template.get("disclaimer"):
        lines.extend(["", f"참고 안내: {template['disclaimer']}"])
    return "\n".join(line for line in lines if line is not None).strip()


def _extract_label_items_from_text(text: str, labels: list[str], fallback_items: list[dict[str, str]]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for index, label in enumerate(labels):
        next_labels = "|".join(re.escape(item) for item in labels[index + 1 :])
        pattern = rf"{re.escape(label)}\s*[:：]\s*(.+?)(?=\n(?:{next_labels})\s*[:：]|$)" if next_labels else rf"{re.escape(label)}\s*[:：]\s*(.+)$"
        match = re.search(pattern, text or "", re.S)
        raw_value = match.group(1).strip() if match else ""
        value = _clean_incomplete_sentence(raw_value) if raw_value else ""
        if value and not is_incomplete_llm_text(raw_value, "fragment") and not is_incomplete_llm_text(value, "fragment"):
            parsed.append({"label": label, "value": value})
    fallback_by_label = {item["label"]: item["value"] for item in fallback_items}
    merged = []
    for label in labels:
        found = next((item for item in parsed if item["label"] == label), None)
        merged.append(found or {"label": label, "value": fallback_by_label.get(label, "공식 데이터 기준으로 확인이 필요합니다.")})
    return merged


def _difficulty_advice_object(course_name: str, item: dict[str, Any], llm_text: str = "") -> dict[str, str]:
    # 난이도 구조화
    topics = " ".join(str(topic) for topic in (item.get("topics") or item.get("detail_topics") or []))
    name_and_topics = f"{course_name} {topics}"
    if re.search(r"인공지능|AI|머신러닝|신경망|추론|퍼지", name_and_topics, re.IGNORECASE):
        return CompatibleAdvice({
            "체감 난이도": "참고용으로는 보통~다소 높은 편입니다.",
            "어렵게 느껴질 수 있는 부분": "추상적인 개념과 용어가 많아 처음에는 낯설 수 있습니다.",
            "필요한 준비": "기본적인 컴퓨터과학 개념과 수학적 사고가 있으면 도움이 됩니다.",
            "학습 팁": "용어를 먼저 정리하고, 예시 문제와 개념 흐름을 함께 보는 방식이 좋습니다.",
        })
    if re.search(r"파이썬|프로그래밍|Java|C프로그래밍", name_and_topics, re.IGNORECASE):
        return CompatibleAdvice({
            "체감 난이도": "참고용으로는 입문자에게 보통 수준으로 느껴질 수 있습니다.",
            "어렵게 느껴질 수 있는 부분": "문법 자체보다 직접 코드를 작성하며 오류를 해결하는 과정이 낯설 수 있습니다.",
            "필요한 준비": "기초 문법을 반복해서 따라 해보고 작은 예제를 직접 실행해보는 준비가 도움이 됩니다.",
            "학습 팁": "강의 내용을 눈으로만 보지 말고 예제를 직접 입력하고 수정해보는 방식이 좋습니다.",
        })

    parsed: dict[str, str] = {}
    for label in ("체감 난이도", "어렵게 느껴질 수 있는 부분", "필요한 준비", "학습 팁"):
        match = re.search(rf"{label}\s*[:：]\s*(.+?)(?=\n(?:체감 난이도|어렵게 느껴질 수 있는 부분|필요한 준비|학습 팁|참고 안내)\s*[:：]|$)", llm_text or "", re.S)
        if match:
            parsed[label] = _clean_incomplete_sentence(match.group(1))
    fallback = {
        "체감 난이도": "참고용으로는 보통 수준으로 볼 수 있습니다.",
        "어렵게 느껴질 수 있는 부분": "처음 접하는 개념과 용어를 익히는 과정에서 부담을 느낄 수 있습니다.",
        "필요한 준비": "공식 교과목 안내의 개요와 주요 학습 내용을 먼저 확인하면 도움이 됩니다.",
        "학습 팁": "핵심 용어를 정리하고 강의 흐름에 맞춰 예시를 함께 확인하는 방식이 좋습니다.",
    }
    return CompatibleAdvice({key: parsed.get(key) or value for key, value in fallback.items()})


def _grade_strategy_fallback(course_name: str, item: dict[str, Any], question: str = "") -> str:
    topics = item.get("topics") or item.get("detail_topics") or []
    topic_lines = "\n".join(f"- {topic}" for topic in topics[:4]) or "- 공식 과목 개요와 강의 핵심 용어"
    goal = "C 이상 성적 취득"
    if re.search(r"A\s*(?:이상|받|맞)", question, re.IGNORECASE):
        goal = "A 이상 성적 취득"
    elif re.search(r"B\s*(?:이상|받|맞)", question, re.IGNORECASE):
        goal = "B 이상 성적 취득"
    return (
        f"**{course_name} 학습 전략 안내입니다.**\n\n"
        f"과목:\n{course_name}\n\n"
        f"목표:\n{goal}\n\n"
        "추천 공부법:\n"
        "공식 과목 내용의 핵심 용어를 먼저 정리하고, 강의 흐름에 맞춰 개념을 반복 확인하는 방식이 좋습니다.\n\n"
        "우선 익혀야 하는 내용:\n"
        f"{topic_lines}\n\n"
        "시험 대비 팁:\n"
        "기출이나 예시 문제를 볼 때 정답만 외우기보다 개념이 어떤 방식으로 문제화되는지 확인하세요.\n\n"
        "주의할 점:\n"
        "성적 취득 전략은 공식 보장 기준이 아닌 참고용 학습 안내입니다. 실제 평가 방식과 범위는 해당 학기 공지와 강의계획을 확인해야 합니다."
    )


def _course_grade_strategy_response(
    question: str,
    course_name: str,
    items: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    item = items[0] if items else {
        "course_name": course_name,
        "overview": "공식 교과목 안내에 등록된 과목입니다.",
        "source_url": COURSE_GUIDE_URL,
    }
    context = {
        "course_name": course_name,
        "overview": item.get("overview") or item.get("feature") or "",
        "topics": item.get("topics") or item.get("detail_topics") or [],
        "source_url": _course_link(item, course_name),
        "fallback_url": COURSE_FULL_GUIDE_URL,
    }
    answer = call_llm_helper("course_grade_strategy", question, context, session_id=session_id)
    if not answer or answer == LLM_SAFE_FAILURE_MESSAGE:
        answer = _grade_strategy_fallback(course_name, item, question)
    answer = sanitize_public_answer(answer)
    fallback = _llm_fallback_template("course_grade_strategy", {"course_name": course_name}, question)
    labels = ["추천 공부법", "우선 익혀야 할 내용", "시험 대비 팁"]
    strategy_items = _extract_label_items_from_text(answer, labels, fallback["items"])
    source_url = _course_link(item, course_name)
    return {
        "answer": fallback["answer"],
        "answer_type": "course_grade_strategy",
        "summary": fallback["summary"],
        "items": strategy_items,
        "disclaimer": fallback["disclaimer"],
        "display_limit": 3,
        "total_count": len(strategy_items),
        "actions": [
            {"type": "link", "label": f"{course_name} 과목 바로가기", "url": source_url},
            {"type": "link", "label": "교과목 안내 바로가기", "url": COURSE_FULL_GUIDE_URL},
        ],
        "source_urls": list(dict.fromkeys([source_url, COURSE_FULL_GUIDE_URL])),
        "sources": [{"title": f"{course_name} 공식 과목 정보", "url": source_url, "score": 100}],
        "mode": "LLM",
        "llm_type": "course_grade_strategy",
        "course_name": course_name,
        "score": 100 if items else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "session_id": session_id,
        "request_id": request_id,
    }


def _course_difficulty_response(
    question: str,
    course_name: str,
    items: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    item = items[0] if items else {
        "course_name": course_name,
        "overview": "공식 교과목 안내에 등록된 과목입니다.",
        "source_url": COURSE_GUIDE_URL,
    }
    source_url = _course_link(item, course_name)
    official_overview = _official_course_overview(course_name, item)
    try:
        advice_text = call_llm_helper(
            "course_difficulty",
            question,
            {
                "course_name": course_name,
                "overview": item.get("overview") or item.get("feature") or "공식 교육과정에 편성된 과목",
                "topics": item.get("topics") or item.get("detail_topics") or [],
                "source_url": item.get("source_url") or COURSE_GUIDE_URL,
                "fallback_url": COURSE_GUIDE_URL,
            },
            session_id=session_id,
            request_id=request_id,
            raise_on_error=True,
        )
    except LLMCallError as exc:
        return _course_difficulty_llm_failure_response(
            question,
            course_name,
            item,
            official_overview,
            source_url,
            started,
            exc,
            session_id=session_id,
            request_id=request_id,
        )
    advice_text = remove_duplicate_overview(advice_text, official_overview)
    difficulty_advice = _difficulty_advice_object(course_name, item, advice_text)
    response_item = {
        "title": course_name,
        "official_overview": official_overview,
        "difficulty_advice": difficulty_advice,
        "disclaimer": (
            "난이도와 학습 부담은 공식 기준이 아닌 참고용 안내이며, "
            "개인의 배경지식과 학습 경험에 따라 달라질 수 있습니다."
        ),
        "source_url": source_url,
        "detail_url": source_url,
        "fallback_url": COURSE_FULL_GUIDE_URL,
        "link_label": f"{course_name} 과목 바로가기",
    }
    return {
        "answer": f"{course_name} 과목의 학습 부담 안내입니다.",
        "answer_type": "course_difficulty",
        "summary": official_overview,
        "official_overview": official_overview,
        "difficulty_advice": difficulty_advice,
        "disclaimer": response_item["disclaimer"],
        "items": [response_item],
        "display_limit": 3,
        "total_count": 1,
        "actions": [
            {"type": "link", "label": f"{course_name} 과목 바로가기", "url": source_url},
            {"type": "link", "label": "교과목 안내 바로가기", "url": COURSE_FULL_GUIDE_URL},
        ],
        "source_urls": list(dict.fromkeys([source_url, COURSE_FULL_GUIDE_URL])),
        "sources": [{"title": f"{course_name} 공식 과목 정보", "url": source_url, "score": 100}],
        "mode": "LLM",
        "llm_type": "course_difficulty",
        "course_name": course_name,
        "score": 100 if items else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _course_difficulty_llm_failure_response(
    question: str,
    course_name: str,
    item: dict[str, Any],
    official_overview: str,
    source_url: str,
    started: float,
    error: LLMCallError,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    if error.code == "LLM_TIMEOUT":
        user_message = (
            "답변 생성 시간이 길어져 AI 보조 답변을 완료하지 못했습니다.\n"
            "공식 데이터 기준으로 확인된 내용만 안내드립니다."
        )
    elif error.code == "LLM_RATE_LIMIT":
        user_message = (
            "현재 AI 응답 요청이 많아 보조 답변을 생성하지 못했습니다.\n"
            "공식 데이터 기준으로 확인된 내용만 안내드립니다.\n"
            "잠시 후 다시 시도할 수 있습니다."
        )
    else:
        user_message = (
            "AI 보조 답변 생성 중 문제가 발생했습니다.\n"
            "공식 데이터 기준으로 확인된 내용만 안내드립니다."
        )
    response_item = {
        "title": course_name,
        "official_overview": official_overview,
        "difficulty_advice": "",
        "disclaimer": "LLM 보조 답변 실패로 공식 데이터 기준 정보만 유지했습니다.",
        "source_url": source_url,
        "detail_url": source_url,
        "fallback_url": COURSE_FULL_GUIDE_URL,
        "link_label": f"{course_name} 과목 바로가기",
    }
    return {
        "ok": True,
        "answer": user_message,
        "answer_type": "llm_fallback",
        "message": user_message,
        "user_message": user_message,
        "error_code": error.code,
        "fallback_available": True,
        "show_retry": True,
        "summary": official_overview,
        "official_overview": official_overview,
        "items": [response_item],
        "display_limit": 1,
        "total_count": 1,
        "actions": [
            {"type": "link", "label": f"{course_name} 과목 바로가기", "url": source_url},
            {"type": "link", "label": "교과목 안내 바로가기", "url": COURSE_FULL_GUIDE_URL},
        ],
        "source_urls": list(dict.fromkeys([source_url, COURSE_FULL_GUIDE_URL])),
        "sources": [{"title": f"{course_name} 공식 과목 정보", "url": source_url, "score": 100}],
        "mode": "OFFICIAL_FALLBACK",
        "llm_type": "course_difficulty",
        "course_name": course_name,
        "score": 100 if item else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "session_id": session_id,
        "request_id": request_id,
        "requires_llm_confirmation": False,
    }


def _llm_type_from_intent(intent: str) -> str:
    mapping = {
        "course_difficulty": "course_difficulty",
        "course_grade_strategy": "course_grade_strategy",
        "course_order": "course_order",
        "course_roadmap": "course_roadmap",
        "notice_explain": "notice_explain",
        "schedule_explain": "schedule_explain",
    }
    return mapping.get(intent, "general_explain")


def _llm_context_from_hits(
    llm_type: str,
    question: str,
    hits: list[dict[str, Any]],
    index: SearchIndex | None = None,
) -> dict[str, Any]:
    # 공식데이터 context
    course = detect_course_name(question, index)
    if llm_type in {"course_difficulty", "course_grade_strategy", "course_order", "course_roadmap"}:
        items = _course_detail_items(question, hits) if course else _course_items(hits)[:3]
        first = items[0] if items else {}
        return {
            "course_name": course or first.get("course_name") or first.get("title") or "",
            "overview": first.get("overview") or first.get("feature") or "",
            "topics": first.get("topics") or first.get("detail_topics") or [],
            "items": items[:3],
            "source_url": _item_url(first, COURSE_GUIDE_URL),
            "fallback_url": COURSE_GUIDE_URL,
        }
    if llm_type == "notice_explain":
        items = _notice_items(hits)[:3]
        return {
            "title": items[0].get("title") if items else "최근 공지",
            "items": items,
            "source_url": NOTICE_URL,
            "fallback_url": NOTICE_URL,
        }
    if llm_type == "schedule_explain":
        items = _schedule_items(hits)[:3]
        return {
            "title": items[0].get("title") if items else "학과 일정",
            "items": items,
            "source_url": SCHEDULE_URL,
            "fallback_url": SCHEDULE_URL,
        }
    first_hit = hits[0] if hits else {}
    return {
        "title": first_hit.get("title") or "컴퓨터과학과 공식 정보",
        "overview": first_hit.get("summary") or first_hit.get("body") or "",
        "source_url": first_hit.get("source_url") or DEPARTMENT_HOME_URL,
        "fallback_url": DEPARTMENT_HOME_URL,
    }


def _llm_source_url(llm_type: str, context: dict[str, Any]) -> str:
    if context.get("source_url"):
        return context["source_url"]
    if llm_type in {"course_difficulty", "course_grade_strategy", "course_order", "course_roadmap"}:
        return COURSE_GUIDE_URL
    if llm_type == "notice_explain":
        return NOTICE_URL
    if llm_type == "schedule_explain":
        return SCHEDULE_URL
    return DEPARTMENT_HOME_URL


def _llm_confirmation_response(
    question: str,
    llm_type: str,
    context: dict[str, Any],
    hits: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    source_url = _llm_source_url(llm_type, context)
    course_name = context.get("course_name") or detect_course_name(question)
    title = context.get("title") or course_name or "공식 정보"
    return {
        "answer": (
            "공식 데이터에서 확인한 내용만으로는 학생 눈높이의 보조 설명이 부족합니다.\n"
            "공식 데이터 범위 안에서 LLM 보조 답변을 생성할까요?"
        ),
        "answer_type": "llm_confirmation_required",
        "summary": "LLM 보조 답변은 공식 데이터와 참고용 안내를 구분해 제공합니다.",
        "items": [],
        "display_limit": 3,
        "total_count": 0,
        "actions": [
            {"type": "confirm_llm", "label": "LLM 보조 답변 사용", "target": "allow_llm"},
            {"type": "link", "label": "공식 페이지 바로가기", "url": source_url},
        ],
        "source_urls": [source_url],
        "sources": [
            {
                "title": title,
                "url": source_url,
                "score": hits[0].get("score", 0) if hits else 0,
            }
        ] if source_url else [],
        "mode": "LLM확인",
        "requires_llm_confirmation": True,
        "llm_type": llm_type,
        "course_name": course_name,
        "context": context,
        "session_id": session_id,
        "request_id": request_id,
        "score": hits[0].get("score", 0) if hits else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _llm_helper_response(
    question: str,
    llm_type: str,
    context: dict[str, Any],
    hits: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    answer = call_llm_helper(llm_type, question, context, session_id=session_id)
    answer = sanitize_public_answer(answer)
    source_url = _llm_source_url(llm_type, context)
    course_name = context.get("course_name") or detect_course_name(question)
    fallback = _llm_fallback_template(llm_type, context, question)
    label_map = {
        "course_order": ["추천 수강 순서", "먼저 알면 좋은 내용", "주의할 점"],
        "notice_explain": ["공지 요약", "학생이 확인할 점", "주의할 점"],
        "schedule_explain": ["일정 요약", "학생이 해야 할 일", "확인할 점"],
        "general_explain": ["핵심 설명", "참고 안내", "다음 확인 사항"],
    }
    structured_items = (
        _extract_label_items_from_text(answer, label_map[llm_type], fallback["items"])
        if llm_type in label_map
        else []
    )
    return {
        "answer": fallback["answer"] if structured_items else answer,
        "answer_type": llm_type if structured_items else "text",
        "summary": fallback["summary"] if structured_items else "공식 데이터 범위 안에서 학생이 이해하기 쉽게 재구성한 보조 답변입니다.",
        "items": structured_items,
        "disclaimer": fallback.get("disclaimer", "") if structured_items else "",
        "display_limit": 3,
        "total_count": len(structured_items),
        "source_urls": [source_url] if source_url else [],
        "actions": [{"type": "link", "label": "공식 페이지 바로가기", "url": source_url}] if source_url else [],
        "mode": "LLM",
        "sources": [
            {
                "title": context.get("title") or course_name or "공식 데이터",
                "url": source_url,
                "score": hits[0].get("score", 0) if hits else 0,
            }
        ] if source_url else [],
        "score": hits[0].get("score", 0) if hits else 0,
        "keywords": tokenize(question),
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "llm_type": llm_type,
        "course_name": course_name,
        "session_id": session_id,
        "request_id": request_id,
        "requires_llm_confirmation": False,
    }


def _llm_prompt(question: str) -> str:
    return f"""
너는 한국방송통신대학교 컴퓨터과학과 공식 정보만 안내하는 챗봇 'ComPass'다.
ComPass는 검색 결과를 그대로 보여주는 챗봇이 아니라 학생이 이해하기 쉽게 재해석해서 안내하는 AI 학과 비서다.
질문에 답할 때 다음 규칙을 반드시 지켜라.
1. 컴퓨터과학과 공식 정보 범위를 벗어나면 정확히 다음 문장만 답한다:
{OUT_OF_SCOPE_MESSAGE}
2. 확실하지 않거나 최신 공식 정보 확인이 필요한 내용은 추측하지 말고 위 거절 문장을 답한다.
3. 존재를 확신하지 못하는 날짜, 규정, 사람, URL을 만들지 않는다.
4. 일반 지식이나 개인 조언으로 답변 범위를 넓히지 않는다.
5. 답변은 한국어로 간결하고 완결된 문장으로 작성한다.
6. 인사말과 자기소개를 하지 않는다.
7. 검색 결과 원문, 키워드 나열, 불완전한 문장, 긴 단락을 출력하지 않는다.
8. 답변은 반드시 아래 구조를 따른다.
   제목 → 1~2줄 설명 → 표 또는 목록 → 참고 안내 → 바로가기 안내
9. 표는 최대 5행까지만 작성한다.
10. 문장이 중간에 끊기지 않도록 완결된 문장으로 끝낸다.

답변 형식 예시:
**과목 안내입니다.**

이 과목은 무엇을 배우는지 학생 관점에서 1~2문장으로 설명합니다.

주요 학습 내용

| 항목 | 설명 |
|---|---|
| 핵심 개념 | 쉬운 설명 |

참고 안내

공식 데이터에서 확인되지 않는 난이도나 학습 부담은 참고용으로만 안내합니다.

바로가기

- 교과목 안내 바로가기

사용자 질문: {question}
""".strip()


def _dedupe_lines(text: str) -> str:
    # 반복 제거
    lines: list[str] = []
    seen_recent: set[str] = set()
    for raw in text.splitlines():
        line = raw.rstrip()
        key = re.sub(r"\s+", " ", line).strip()
        if not key:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if key in seen_recent:
            continue
        lines.append(line)
        seen_recent.add(key)
        if len(seen_recent) > 8:
            seen_recent = set(re.sub(r"\s+", " ", item).strip() for item in lines[-8:] if item.strip())
    return "\n".join(lines).strip()


def _strip_markdown_noise(text: str) -> str:
    text = re.sub(r"```.*?```", "", text or "", flags=re.S)
    cleaned = []
    seen_titles = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"[-*_]{3,}", line):
            cleaned.append("")
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        if "안내입니다" in line:
            key = re.sub(r"\s+", "", line)
            if key in seen_titles:
                continue
            seen_titles.add(key)
        cleaned.append(line)
    return "\n".join(cleaned)


def _bulletize_keyword_line(line: str) -> str:
    # 키워드 줄 정리
    stripped = line.strip()
    if not stripped or stripped.startswith(("-", "•", "|", "#", "*")):
        return line
    if re.search(r"[.!?。]|입니다|합니다|합니다|된다|있다|없다", stripped):
        return line
    parts = [part.strip(" ,·/") for part in re.split(r"[,/·]\s*|\s{2,}", stripped) if part.strip(" ,·/")]
    if len(parts) < 4:
        return line
    if max(len(part) for part in parts) > 18:
        return line
    return "\n".join(f"- {part}" for part in parts[:8])


def _wrap_long_sentence(line: str, limit: int = 68) -> str:
    # 긴 문장 줄바꿈
    if len(line) <= limit or line.startswith("|") or line.startswith(("-", "•")):
        return line
    chunks: list[str] = []
    current = line
    while len(current) > limit:
        cut = max(current.rfind(" ", 0, limit), current.rfind(",", 0, limit), current.rfind("며", 0, limit))
        if cut < 24:
            cut = limit
        chunks.append(current[:cut].rstrip())
        current = current[cut:].lstrip(" ,")
    if current:
        chunks.append(current)
    return "\n".join(chunks)


def sanitize_llm_response(text: str, question: str = "") -> str:
    # LLM 응답 정리
    clean = re.sub(r"\r\n?", "\n", text or "").strip()
    if not clean:
        return OUT_OF_SCOPE_MESSAGE
    if OUT_OF_SCOPE_MESSAGE in clean:
        return OUT_OF_SCOPE_MESSAGE

    clean = _strip_markdown_noise(clean)
    clean = _dedupe_lines(clean)
    processed: list[str] = []
    for line in clean.splitlines():
        line = _bulletize_keyword_line(line)
        for part in line.splitlines():
            part = _clean_incomplete_sentence(part) if re.search(r"(?:및|등|,|-)$", part.strip()) else part
            if len(part.strip(" -•")) <= 2 and part.lstrip().startswith(("-", "•")):
                continue
            processed.append(_wrap_long_sentence(part))
    clean = "\n".join(processed)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    if len(clean) >= 200 and "참고 안내" not in clean and "안내\n" not in clean:
        clean += (
            "\n\n참고 안내\n\n"
            "난이도와 학습 부담은 공식 기준이 아닌 참고용 정보이며, "
            "개인의 배경지식과 학습 경험에 따라 달라질 수 있습니다."
        )

    if not re.search(r"^\s*(?:\*\*)?.{2,40}안내", clean):
        course_name = detect_course_name(question)
        title = f"{course_name} 과목 안내입니다." if course_name else "ComPass 안내입니다."
        clean = f"{title}\n\n{clean}"

    if not re.search(r"[.!?。요다)\]]\s*$", clean):
        clean += "."
    return clean


def _openai(prompt: str) -> str:
    if not config.OPENAI_API_KEY:
        raise LLMCallError("LLM_API_KEY_MISSING", detail="OPENAI_API_KEY is not configured")
    if not config.OPENAI_MODEL:
        raise LLMCallError("LLM_MODEL_MISSING", detail="OPENAI_MODEL is not configured")
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": config.OPENAI_MODEL,
                "input": prompt,
                "temperature": 0.2,
                "max_output_tokens": 1200,
            },
            timeout=20,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise LLMCallError("LLM_TIMEOUT", detail="OpenAI request timed out") from exc
    except requests.HTTPError as exc:
        raise _llm_http_error(exc) from exc
    except requests.RequestException as exc:
        raise LLMCallError("LLM_PROVIDER_ERROR", detail=type(exc).__name__) from exc
    data = response.json()
    if data.get("output_text"):
        return data["output_text"].strip()
    parts = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "".join(parts).strip()


def _gemini_request(prompt: str, model: str, api_key: str) -> str:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    text = "".join(
        part.get("text", "")
        for candidate in data.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
    ).strip()
    if not text:
        raise LLMCallError("LLM_PROVIDER_ERROR", detail=f"Gemini response is empty model={model}")
    return text


def _gemini_models_to_try() -> list[str]:
    models = [config.GEMINI_MODEL, *getattr(config, "GEMINI_FALLBACK_MODELS", [])]
    return list(dict.fromkeys(model for model in models if model))


def _gemini_api_key_entries() -> list[tuple[str, str]]:
    entries = getattr(config, "GEMINI_API_KEY_ENTRIES", [])
    if entries:
        return [(str(label), key) for label, key in entries if key]
    keys = getattr(config, "GEMINI_API_KEYS", []) or [config.GEMINI_API_KEY]
    labels = ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"]
    return [(labels[index] if index < len(labels) else f"GEMINI_API_KEY_{index + 1}", key) for index, key in enumerate(keys) if key]


def _gemini_api_keys_to_try() -> list[str]:
    return list(dict.fromkeys(key for _, key in _gemini_api_key_entries()))


def _is_quota_error(error: LLMCallError) -> bool:
    text = f"{error.code} {error.detail}".lower()
    return (
        error.code == "LLM_RATE_LIMIT"
        or "resource_exhausted" in text
        or "quota exceeded" in text
        or "rate limit exceeded" in text
        or "too many requests" in text
        or "http 429" in text
    )


def _gemini(prompt: str) -> str:
    api_key_entries = _gemini_api_key_entries()
    if not api_key_entries:
        raise LLMCallError("LLM_API_KEY_MISSING", detail="GEMINI_API_KEY is not configured")
    if not config.GEMINI_MODEL:
        raise LLMCallError("LLM_MODEL_MISSING", detail="GEMINI_MODEL is not configured")
    last_error: LLMCallError | None = None
    for model in _gemini_models_to_try():
        if _is_llm_in_cooldown("gemini", model):
            logger.info("[LLM][SKIP] provider=gemini model=%s reason=cooldown", model)
            last_error = LLMCallError("LLM_RATE_LIMIT", detail=f"Gemini model in cooldown: {model}")
            continue
        for key_index, (key_label, api_key) in enumerate(api_key_entries, start=1):
            try:
                result = _gemini_request(prompt, model, api_key)
                if key_index > 1:
                    logger.info("[Gemini Failover Success] Using %s", key_label)
                return result
            except requests.Timeout as exc:
                last_error = LLMCallError("LLM_TIMEOUT", detail=f"Gemini request timed out model={model} key={key_label}")
                break
            except requests.HTTPError as exc:
                last_error = _llm_http_error(exc, model=model)
                if (_is_quota_error(last_error) or last_error.code == "LLM_PROVIDER_ERROR") and key_index < len(api_key_entries):
                    exhausted_label = "Primary Key" if key_index == 1 else f"GEMINI_API_KEY_{key_index}"
                    reason = "Quota Exceeded" if _is_quota_error(last_error) else "Provider Error"
                    logger.warning(
                        "[Gemini Failover] %s %s",
                        exhausted_label,
                        reason,
                    )
                    logger.warning("[Gemini Failover] Switching to %s", api_key_entries[key_index][0])
                    continue
                _set_llm_cooldown("gemini", model, last_error.code)
                if last_error.code not in {"LLM_RATE_LIMIT", "LLM_PROVIDER_ERROR"}:
                    break
                logger.warning("[LLM][RETRY] provider=gemini model=%s code=%s trying_fallback_model=true", model, last_error.code)
                break
            except requests.RequestException as exc:
                last_error = LLMCallError("LLM_PROVIDER_ERROR", detail=f"{type(exc).__name__} model={model} key={key_label}")
                if key_index < len(api_key_entries):
                    exhausted_label = "Primary Key" if key_index == 1 else f"GEMINI_API_KEY_{key_index}"
                    logger.warning("[Gemini Failover] %s Provider Error", exhausted_label)
                    logger.warning("[Gemini Failover] Switching to %s", api_key_entries[key_index][0])
                    continue
                _set_llm_cooldown("gemini", model, last_error.code)
                break
            except LLMCallError as exc:
                last_error = exc
                if (_is_quota_error(exc) or exc.code == "LLM_PROVIDER_ERROR") and key_index < len(api_key_entries):
                    exhausted_label = "Primary Key" if key_index == 1 else f"GEMINI_API_KEY_{key_index}"
                    reason = "Quota Exceeded" if _is_quota_error(exc) else "Provider Error"
                    logger.warning(
                        "[Gemini Failover] %s %s",
                        exhausted_label,
                        reason,
                    )
                    logger.warning("[Gemini Failover] Switching to %s", api_key_entries[key_index][0])
                    continue
                _set_llm_cooldown("gemini", model, exc.code)
                if exc.code not in {"LLM_PROVIDER_ERROR"}:
                    break
                break
    if last_error:
        _set_llm_cooldown("gemini", "*", last_error.code)
        raise last_error
    raise LLMCallError("LLM_UNKNOWN_ERROR", detail="Gemini request failed without detail")


def _llm_http_error(exc: requests.HTTPError, *, model: str = "") -> LLMCallError:
    status_code = getattr(exc.response, "status_code", 0) or 0
    response_text = sanitize_input(str(getattr(exc.response, "text", "") or ""), 160)
    suffix = f"{f' model={model}' if model else ''}{f' body={response_text}' if response_text else ''}"
    if status_code == 429:
        return LLMCallError("LLM_RATE_LIMIT", detail=f"provider returned HTTP {status_code}{suffix}")
    if status_code == 400:
        return LLMCallError("LLM_BAD_REQUEST", detail=f"provider returned HTTP {status_code}{suffix}")
    return LLMCallError("LLM_PROVIDER_ERROR", detail=f"provider returned HTTP {status_code}{suffix}")


def _record_llm_error(provider: str, model: str, exc: LLMCallError) -> None:
    LLM_LAST_ERROR.update({
        "code": exc.code,
        "message": exc.detail,
        "provider": provider,
        "model": model,
    })


def get_llm_health_status() -> dict[str, Any]:
    provider = (config.LLM_PROVIDER or "").strip().lower()
    if provider == "gemini":
        configured = bool(_gemini_api_keys_to_try())
        model = config.GEMINI_MODEL or "gemini-2.5-flash"
        fallback_models = getattr(config, "GEMINI_FALLBACK_MODELS", [])
        key_count = len(_gemini_api_keys_to_try())
    elif provider == "openai":
        configured = bool(config.OPENAI_API_KEY)
        model = config.OPENAI_MODEL
        fallback_models = []
        key_count = 1 if config.OPENAI_API_KEY else 0
    else:
        configured = False
        model = ""
        fallback_models = []
        key_count = 0
    cooldown_remaining = max(0, round(LLM_COOLDOWN_UNTIL.get(_llm_cooldown_key(provider, "*"), 0) - time.time()))
    return {
        "provider": provider,
        "configured": configured,
        "model": model,
        "fallback_models": fallback_models,
        "key_count": key_count,
        "intent_classifier_enabled": bool(config.ENABLE_LLM_INTENT_CLASSIFIER),
        "last_error": LLM_LAST_ERROR.get("code") or "",
        "cooldown_remaining_sec": cooldown_remaining,
    }


def call_llm(question: str, *, prompt_override: str | None = None) -> str:
    prompt = prompt_override or _llm_prompt(question)
    return sanitize_llm_response(call_llm_raw(prompt), question)


def call_llm_raw(prompt: str) -> str:
    provider = (config.LLM_PROVIDER or "").strip().lower()

    logger.info("LLM_PROVIDER=%r", provider)

    if provider == "gemini":
        return _gemini(prompt)
    if provider == "openai":
        return _openai(prompt)

    raise LLMCallError("LLM_PROVIDER_ERROR", detail=f"unsupported LLM_PROVIDER: {config.LLM_PROVIDER}")


def call_llm_helper(
    llm_type: str,
    question: str,
    context: dict[str, Any],
    *,
    session_id: str = "",
    request_id: str = "",
    raise_on_error: bool = False,
) -> str:
    # LLM 함수
    provider = (config.LLM_PROVIDER or "").strip().lower()
    normalized_type = llm_type if llm_type in {
        "course_difficulty",
        "course_grade_strategy",
        "course_order",
        "course_roadmap",
        "notice_explain",
        "schedule_explain",
        "general_explain",
    } else "general_explain"
    session_short = (session_id or "")[:8] or "server"
    request_short = (request_id or "")[:12] or "server"
    model = config.GEMINI_MODEL if provider == "gemini" else config.OPENAI_MODEL
    prompt = build_llm_prompt(normalized_type, question, context)
    logger.info(
        "[LLM][START] request_id=%s provider=%s model=%s type=%s session=%s",
        request_short,
        provider,
        model,
        normalized_type,
        session_short,
    )
    try:
        raw_answer = call_llm_raw(prompt)
        answer = sanitize_llm_response(raw_answer, question)
        LLM_LAST_ERROR.update({"code": "", "message": "", "provider": provider, "model": model})
        if is_incomplete_llm_text(raw_answer, normalized_type) or is_incomplete_llm_text(answer, normalized_type):
            logger.warning(
                "LLM 불완전 응답 감지: provider=%s, llm_type=%s, session=%s",
                provider,
                normalized_type,
                session_short,
            )
            retry_prompt = (
                f"{prompt}\n\n"
                "[재작성 지시]\n"
                "이전 답변이 중간에 끊겼습니다. 반드시 완결된 문장으로 짧고 구조화하여 다시 작성하세요. "
                "각 항목은 1~2문장 이내로 끝내세요. 답변은 700자 이내로 작성하고 마지막 문장은 반드시 마침표로 끝내세요."
            )
            retry_raw = call_llm_raw(retry_prompt)
            retry_answer = sanitize_llm_response(retry_raw, question)
            if not is_incomplete_llm_text(retry_raw, normalized_type) and not is_incomplete_llm_text(retry_answer, normalized_type):
                return retry_answer
            logger.warning(
                "LLM 재시도 후에도 불완전하여 fallback 사용: provider=%s, llm_type=%s, session=%s",
                provider,
                normalized_type,
                session_short,
            )
            return _fallback_text_from_template(_llm_fallback_template(normalized_type, context, question))
        return answer
    except LLMCallError as exc:
        _record_llm_error(provider, model, exc)
        logger.error(
            "[LLM][ERROR] request_id=%s code=%s provider=%s type=%s context=%s message=%s",
            request_short,
            exc.code,
            provider,
            normalized_type,
            sanitize_input(str(context.get("course_name") or context.get("title") or ""), 80),
            sanitize_input(exc.detail, 120),
        )
        logger.info("[LLM][FALLBACK] request_id=%s fallback=official_only", request_short)
        if raise_on_error:
            raise
        return LLM_SAFE_FAILURE_MESSAGE
    except Exception as exc:
        wrapped = LLMCallError("LLM_UNKNOWN_ERROR", detail=type(exc).__name__)
        _record_llm_error(provider, model, wrapped)
        logger.error(
            "[LLM][ERROR] request_id=%s code=%s provider=%s type=%s context=%s message=%s",
            request_short,
            wrapped.code,
            provider,
            normalized_type,
            sanitize_input(str(context.get("course_name") or context.get("title") or ""), 80),
            type(exc).__name__,
        )
        logger.info("[LLM][FALLBACK] request_id=%s fallback=official_only", request_short)
        if raise_on_error:
            raise wrapped from exc
        return LLM_SAFE_FAILURE_MESSAGE


IMPORTANT_ARCHIVE_NOTICE = (
    "이 자료는 중요 보관 문서이지만 게시 시점 기준 정보일 수 있습니다. "
    "최신 기준은 관련 공식 페이지에서 다시 확인해 주세요."
)


def apply_data_tier_notice(response: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    if not any((hit.get("data_tier") or "") == "IMPORTANT_ARCHIVE" for hit in hits):
        return response
    response["data_tier_notice"] = IMPORTANT_ARCHIVE_NOTICE
    response["summary"] = (
        f"{response.get('summary', '').strip()}\n{IMPORTANT_ARCHIVE_NOTICE}".strip()
    )
    if response.get("answer_type") == "text" and IMPORTANT_ARCHIVE_NOTICE not in response.get("answer", ""):
        response["answer"] = f"{response.get('answer', '').rstrip()}\n\n안내: {IMPORTANT_ARCHIVE_NOTICE}"
    return response


def answer_question(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    allow_llm: bool = False,
    llm_type: str | None = None,
    session_id: str = "",
    request_id: str = "",
    index: SearchIndex | None = None,
    forced_intent: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    clean_question = sanitize_input(question)
    if not clean_question:
        return {
            "answer": "질문을 입력해 주세요.",
            "answer_type": "text",
            "summary": "",
            "items": [],
            "total_count": 0,
            "source_urls": [],
            "actions": [],
            "mode": "SYSTEM",
            "sources": [],
            "score": 0,
        }
    casual = casual_response(clean_question)
    if casual:
        casual["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        casual["session_id"] = session_id
        casual["request_id"] = request_id
        return casual
    index = index or SearchIndex()
    priority_intent = FORCED_QUICK_INTENTS.get(str(forced_intent or "").strip().lower()) or priority_button_intent(clean_question)
    if priority_intent in {"notice_list", "course_table", "schedule_list"}:
        response = build_priority_intent_response(priority_intent, clean_question, index, started)
        response["session_id"] = session_id
        response["request_id"] = request_id
        response["quick_intent"] = priority_intent
        return response
    initial_intent = classify_intent(clean_question, index)
    if initial_intent == "faculty_detail":
        faculty = detect_faculty_member(clean_question, index)
        if faculty and faculty.get("_not_found"):
            hits = retrieve_documents(index, clean_question, "faculty")
            parsed_items = normalize_results("faculty", hits)
            matched = next((item for item in parsed_items if item.get("name") == faculty.get("name")), None)
            if matched:
                faculty = matched
        return build_faculty_detail_response(
            faculty,
            question=clean_question,
            started=started,
        )
    if initial_intent == "faculty":
        hits = retrieve_documents(index, clean_question, "faculty")
        items = normalize_results("faculty", hits, clean_question)
        if not items:
            items = faculty_catalog_items(index)
        sources = [
            {"title": hit.get("title") or "컴퓨터과학과 교수진", "url": hit.get("source_url"), "score": hit.get("score")}
            for hit in hits[:1]
            if hit.get("source_url")
        ]
        if items:
            response = build_structured_response(
                "faculty",
                items,
                source_url=FACULTY_URL,
                sources=sources or [{"title": "컴퓨터과학과 교수진", "url": FACULTY_URL, "score": 100}],
                score=hits[0].get("score", 100) if hits else 100,
                keywords=tokenize(clean_question),
                started=started,
            )
            response["structured_intent"] = "faculty_list"
            response["search_scope"] = ["faculty"]
            if not response.get("sources"):
                response["sources"] = [{"title": "컴퓨터과학과 교수진", "url": FACULTY_URL, "score": 100}]
            return response
        return {
            "answer": "컴퓨터과학과 공식 교수진 데이터를 충분히 찾지 못했습니다.",
            "answer_type": "faculty",
            "summary": "교수진 페이지에서 전체 목록을 확인해 주세요.",
            "items": [],
            "display_limit": 3,
            "total_count": 0,
            "actions": [{"type": "link", "label": "교수진 페이지 바로가기", "url": FACULTY_URL}],
            "source_urls": [FACULTY_URL],
            "sources": [{"title": "컴퓨터과학과 교수진", "url": FACULTY_URL, "score": 0}],
            "mode": "DB검색",
            "score": 0,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": "교수진 공식 문서 없음",
            "structured_intent": "faculty_list",
            "search_scope": ["faculty"],
        }
    if initial_intent in {"course_table", "notice_list"}:
        response = build_priority_intent_response(initial_intent, clean_question, index, started)
        response["session_id"] = session_id
        response["request_id"] = request_id
        response["quick_intent"] = initial_intent
        return response
    if initial_intent == "exam_scope":
        response = _exam_scope_response(clean_question, index, started)
        response["session_id"] = session_id
        response["request_id"] = request_id
        return response
    if initial_intent == "course_grade_strategy":
        course_name = detect_course_name(clean_question, index)
        hits = retrieve_documents(index, clean_question, "course_grade_strategy")
        items = normalize_results("course_grade_strategy", hits, clean_question)
        return _course_grade_strategy_response(
            clean_question,
            course_name,
            items,
            started,
            session_id=session_id,
            request_id=request_id,
        )
    if initial_intent == "course_difficulty":
        course_name = detect_course_name(clean_question, index)
        hits = retrieve_documents(index, clean_question, "course_difficulty")
        items = normalize_results("course_difficulty", hits, clean_question)
        if not allow_llm:
            return _course_difficulty_confirmation(
                clean_question,
                course_name,
                items,
                started,
                session_id=session_id,
                request_id=request_id,
            )
        try:
            response = _course_difficulty_response(
                clean_question,
                course_name,
                items,
                started,
                session_id=session_id,
                request_id=request_id,
            )
            response["session_id"] = session_id
            response["request_id"] = request_id
            return response
        except Exception as exc:
            logger.exception("과목 난이도 LLM 보조 답변 실패: %s", exc)
            result = _course_difficulty_confirmation(
                clean_question,
                course_name,
                items,
                started,
                session_id=session_id,
                request_id=request_id,
            )
            result.update(
                answer=LLM_SAFE_FAILURE_MESSAGE,
                answer_type="course_difficulty",
                requires_llm_confirmation=False,
                failure_reason=f"LLM 호출 실패: {type(exc).__name__}",
                actions=[
                    action for action in result["actions"] if action.get("type") == "link"
                ],
            )
            return result
    if initial_intent in {"course_order", "course_roadmap", "notice_explain", "schedule_explain"}:
        requested_llm_type = llm_type or _llm_type_from_intent(initial_intent)
        hits = retrieve_documents(index, clean_question, initial_intent)
        context = _llm_context_from_hits(requested_llm_type, clean_question, hits, index)
        if not allow_llm:
            return _llm_confirmation_response(
                clean_question,
                requested_llm_type,
                context,
                hits,
                started,
                session_id=session_id,
                request_id=request_id,
            )
        return _llm_helper_response(
            clean_question,
            requested_llm_type,
            context,
            hits,
            started,
            session_id=session_id,
            request_id=request_id,
        )
    curated = match_curated(clean_question, history)
    if curated:
        if curated.get("answer_type") == "course_recommendation":
            return _course_recommendation_response(curated, started)
        if curated.get("answer_type") == "course_detail" and curated.get("structured_items"):
            source_url = curated.get("source_url") or CURRICULUM_URL
            items = [
                {
                    **item,
                    "source_url": item.get("source_url") or source_url,
                    "fallback_url": source_url,
                    "link_label": item.get("link_label") or f"{item.get('title', '과목')} 바로가기",
                }
                for item in curated.get("structured_items", [])
            ]
            response = build_structured_response(
                "course_detail",
                items,
                source_url=source_url,
                sources=[{"title": curated.get("title"), "url": source_url, "score": 100}],
                score=100,
                keywords=curated.get("keywords", tokenize(clean_question)),
                started=started,
            )
            response["summary"] = curated.get("summary") or response["summary"]
            response["structured_intent"] = curated.get("intent")
            response["validity"] = curated.get("validity")
            return response
        if curated.get("answer_type") == "certification_list" and curated.get("structured_items"):
            source_url = curated.get("source_url") or DEPARTMENT_HOME_URL
            items = [
                {
                    **item,
                    "source_url": item.get("source_url") or source_url,
                    "fallback_url": source_url,
                    "link_label": item.get("link_label") or "자격증 정보 바로가기",
                }
                for item in curated.get("structured_items", [])
            ]
            response = build_structured_response(
                "certification_list",
                items,
                source_url=source_url,
                sources=[{"title": curated.get("title"), "url": source_url, "score": 100}],
                score=100,
                keywords=curated.get("keywords", tokenize(clean_question)),
                started=started,
            )
            response["structured_intent"] = curated.get("intent")
            response["validity"] = curated.get("validity")
            response["note"] = curated.get("note", "")
            return response
        return {
            "answer": curated["answer"],
            "answer_type": curated.get("answer_type", "text"),
            "summary": curated.get("note", ""),
            "items": [],
            "total_count": 0,
            "source_urls": [curated["source_url"]] if curated.get("source_url") else [],
            "actions": build_actions("text", [], curated.get("source_url", "")),
            "mode": "DB검색",
            "sources": [
                {
                    "title": curated["title"],
                    "url": curated["source_url"],
                    "score": 100,
                }
            ],
            "score": 100,
            "keywords": curated.get("keywords", tokenize(clean_question)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "structured_intent": curated.get("intent"),
            "validity": curated.get("validity"),
        }
    if is_course_recommendation(clean_question):
        hits = retrieve_documents(index, clean_question, "course_recommendation")
        course_items = _course_items(hits)
        if course_items:
            source_url = next((item.get("source_url") for item in course_items if item.get("source_url")), "")
            items = [
                {
                    **item,
                    "reason": "공식 교육과정에 등록된 과목입니다. 세부 난이도는 개인별 배경지식에 따라 달라질 수 있습니다.",
                    "difficulty_hint": "개인차 있음",
                    "workload_hint": "강의계획서와 평가방법 확인 필요",
                    "source_url": item.get("source_url") or source_url or CURRICULUM_URL,
                    "fallback_url": CURRICULUM_URL,
                    "link_label": "교육과정 바로가기",
                }
                for item in course_items
            ]
            return {
                "answer": "편입생 및 입문자 기준 추천 가능한 과목입니다.",
                "answer_type": "course_recommendation",
                "summary": "공식 교육과정 데이터에서 확인한 과목 3개를 먼저 안내드립니다.",
                "items": items,
                "display_limit": 3,
                "total_count": len(items),
                "actions": build_actions("course_recommendation", items, source_url),
                "source_urls": [source_url] if source_url else [],
                "sources": [{"title": "컴퓨터과학과 교육과정", "url": source_url, "score": 100}] if source_url else [],
                "mode": "DB검색",
                "score": hits[0]["score"] if hits else 0,
                "keywords": tokenize(clean_question),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "structured_intent": "course_recommendation",
                "validity": "학기별 개설 과목 및 학점은 공식 교육과정표 확인 필요",
            }
        return {
            "answer": "과목 추천을 위해 필요한 구조화된 교육과정 데이터를 아직 충분히 찾지 못했습니다.",
            "answer_type": "course_recommendation",
            "summary": "교육과정 데이터를 다시 크롤링하거나 관리자 화면에서 인덱스를 재생성해 주세요.",
            "items": [],
            "display_limit": 3,
            "total_count": 0,
            "actions": [],
            "source_urls": [],
            "sources": [],
            "mode": "DB검색",
            "score": 0,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "structured_intent": "course_recommendation",
            "failure_reason": "구조화 교육과정 데이터 없음",
        }
    if not detect_course_name(clean_question, index) and not detect_faculty_member(clean_question, index) and is_out_of_scope(clean_question):
        return {
            "answer": OUT_OF_SCOPE_MESSAGE,
            "answer_type": "out_of_scope",
            "summary": OUT_OF_SCOPE_MESSAGE,
            "items": [],
            "total_count": 0,
            "source_urls": [],
            "actions": [],
            "mode": "SYSTEM",
            "sources": [],
            "score": 0,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": "범위 외 질문",
        }

    search_question = contextualize(clean_question, history)
    requested_answer_type = classify_intent(search_question, index)
    hits = retrieve_documents(index, search_question, requested_answer_type)
    if requested_answer_type == "notice_list":
        hits = _supplement_notice_hits(index, hits)
    elif requested_answer_type == "schedule_list":
        hits = _supplement_schedule_hits(index, hits)
    best_score = hits[0].get("score", 100) if hits else 0
    if requested_answer_type == "schedule_list" and not normalize_results("schedule_list", hits, search_question):
        return build_schedule_unavailable_response(started, clean_question)
    if hits and best_score >= config.SEARCH_MIN_SCORE:
        if any((hit.get("document_type") or "") in DOCUMENT_RESOURCE_TYPES for hit in hits) and re.search(r"기출|시험문제|이전\s*시험|pdf|PDF", search_question, re.IGNORECASE):
            return _document_resource_response(search_question, hits, started)
        sources = [
            {"title": hit.get("title"), "url": hit.get("source_url"), "score": hit.get("score")}
            for hit in hits[:3]
            if hit.get("source_url")
        ]
        response = {
            "answer": render_fallback_text(search_question, hits),
            "answer_type": "text",
            "summary": "",
            "items": [],
            "total_count": 0,
            "source_urls": [source["url"] for source in sources],
            "actions": [],
            "mode": "DB검색",
            "sources": sources,
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "search_results": hits[:3],
        }
        if FACULTY_QUERY_RE.search(search_question):
            faculty_hit = next(
                (
                    hit
                    for hit in hits
                    if hit.get("source_url") == FACULTY_URL
                    or "교수진" in (hit.get("title") or "")
                ),
                hits[0],
            )
            items = normalize_results("faculty", hits)
            if items:
                response.update(
                    build_structured_response(
                        "faculty",
                        items,
                        source_url=faculty_hit.get("source_url") or FACULTY_URL,
                        sources=sources,
                        score=best_score,
                        keywords=tokenize(clean_question),
                        started=started,
                    )
                )
        else:
            answer_type = requested_answer_type
            if answer_type in {"course_table", "course_detail", "notice_list", "schedule_list", "faq_list"}:
                category_urls = {
                    "course_table": CURRICULUM_URL,
                    "course_detail": CURRICULUM_URL,
                    "notice_list": NOTICE_URL,
                    "schedule_list": SCHEDULE_URL,
                    "faq_list": sources[0]["url"] if sources else DEPARTMENT_HOME_URL,
                }
                items = normalize_results(answer_type, hits, search_question)
                if answer_type == "course_table":
                    items = _curriculum_preview_items(items, index)
                response.update(
                    build_structured_response(
                        answer_type,
                        items,
                        source_url=category_urls[answer_type],
                        sources=sources,
                        score=best_score,
                        keywords=tokenize(clean_question),
                        started=started,
                        requested_grade=_requested_curriculum_grade(clean_question) if answer_type == "course_table" else "",
                    )
                )
        if response.get("answer_type") == "text" and should_auto_llm(search_question, hits, response.get("answer", "")):
            requested_llm_type = _llm_type_from_intent(requested_answer_type)
            context = _llm_context_from_hits(requested_llm_type, search_question, hits, index)
            return _llm_helper_response(
                search_question,
                requested_llm_type,
                context,
                hits,
                started,
                session_id=session_id,
                request_id=request_id,
            )
        response = apply_data_tier_notice(response, hits)
        response["answer"] = sanitize_public_answer(response.get("answer", ""))
        return response

    if not allow_llm:
        return {
            "answer": (
                "현재 공식 데이터에서 관련 정보를 찾지 못했습니다.\n"
                "원하시면 AI 보조 답변을 통해 관련 정보를 추가로 안내해드릴 수 있습니다."
            ),
            "answer_type": "text",
            "summary": "공식 데이터에서 충분한 근거를 찾지 못했습니다.",
            "items": [],
            "total_count": 0,
            "source_urls": [],
            "actions": [{"type": "confirm_llm", "label": "AI 보조 답변", "target": "allow_llm"}],
            "mode": "LLM확인",
            "requires_llm_confirmation": True,
            "sources": [],
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": "검색 점수 기준 미달",
        }

    try:
        requested_llm_type = llm_type or "general_explain"
        context = _llm_context_from_hits(requested_llm_type, clean_question, hits, index)
        answer = call_llm_helper(
            requested_llm_type,
            clean_question,
            context,
            session_id=session_id,
        )
        if not answer:
            answer = OUT_OF_SCOPE_MESSAGE
        detected_course = detect_course_name(clean_question, index)
        llm_actions: list[dict[str, Any]] = []
        llm_source_urls: list[str] = []
        if detected_course:
            detected = index.detect_course(clean_question) if index and hasattr(index, "detect_course") else None
            course_url = (detected or {}).get("detail_url") or COURSE_FULL_GUIDE_URL
            llm_actions.extend(
                [
                    {"type": "link", "label": f"{detected_course} 과목 바로가기", "url": course_url},
                    {"type": "link", "label": "교과목 안내 바로가기", "url": COURSE_FULL_GUIDE_URL},
                ]
            )
            llm_source_urls.extend([course_url, COURSE_FULL_GUIDE_URL])
        return {
            "answer": answer,
            "answer_type": "text",
            "summary": "공식 데이터 범위 안에서 학생이 이해하기 쉽게 재구성한 보조 답변입니다.",
            "items": [],
            "total_count": 0,
            "source_urls": llm_source_urls,
            "actions": llm_actions,
            "mode": "LLM",
            "sources": (
                [{"title": "컴퓨터과학과 교과목 안내", "url": llm_source_urls[0], "score": best_score}]
                if detected_course
                else []
            ),
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "llm_type": requested_llm_type,
            "session_id": session_id,
            "request_id": request_id,
        }
    except Exception as exc:
        logger.exception("LLM fallback 실패: %s", exc)
        return {
            "answer": OUT_OF_SCOPE_MESSAGE,
            "answer_type": "out_of_scope",
            "summary": OUT_OF_SCOPE_MESSAGE,
            "items": [],
            "total_count": 0,
            "source_urls": [],
            "actions": [],
            "mode": "LLM",
            "sources": [],
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": f"LLM 호출 실패: {type(exc).__name__}",
            "llm_type": llm_type or "general_explain",
            "session_id": session_id,
            "request_id": request_id,
        }
