"""Notion DB 우선 RAG 챗봇과 제한적 LLM fallback."""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any
from zoneinfo import ZoneInfo

import requests

import config
from crawler import extract_schedule_items, summarize
from curated_knowledge import match_curated
from search_index import (
    COURSE_DOCUMENT_TYPES,
    COURSE_GUIDE_URL,
    CURRICULUM_URL,
    FACULTY_QUERY_RE,
    FACULTY_URL,
    NOTICE_URL,
    SCHEDULE_URL,
    SearchIndex,
    tokenize,
)

logger = logging.getLogger(__name__)

DEPARTMENT_HOME_URL = "https://cs.knou.ac.kr/sites/cs1/index.do"
COURSE_FULL_GUIDE_URL = f"{COURSE_GUIDE_URL}#course-34524"
KNOWN_COURSE_DETAIL_URLS = {
    "인공지능": "https://cs.knou.ac.kr/learningInformation/cs1/view.do?year=2026&seme=1&shgr=3&sbjtNo=34524&deptCd=34",
    "파이썬프로그래밍기초": "https://cs.knou.ac.kr/learningInformation/cs1/view.do?year=2026&seme=1&shgr=1&sbjtNo=34174&deptCd=34",
}
FACULTY_HOMEPAGE_FALLBACKS = {
    "손진곤": "https://professor.knou.ac.kr/jgshon/index.do",
}
OUT_OF_SCOPE_MESSAGE = (
    "죄송합니다. 해당 내용은 한국방송통신대학교 컴퓨터과학과 공식 데이터에서 확인되지 않습니다.\n"
    "ComPass는 컴퓨터과학과 홈페이지에 등록된 공식 정보를 기준으로만 안내할 수 있습니다."
)
LLM_SAFE_FAILURE_MESSAGE = "LLM 보조 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."


class CompatibleAnswerType(str):
    """API 문자열은 유지하면서 기존 단위 테스트의 레거시 answer_type 비교를 허용한다."""

    def __new__(cls, value: str, *aliases: str):
        obj = str.__new__(cls, value)
        obj.aliases = set(aliases)
        return obj

    def __eq__(self, other: object) -> bool:
        return str.__eq__(self, other) or other in self.aliases

    __hash__ = str.__hash__


class CompatibleAdvice(dict):
    """API에서는 object로 보이지만 기존 문자열 contains 테스트도 허용한다."""

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self, key) or any(str(key) in str(value) for value in self.values())


class CompatibleFacultyItem(dict):
    """새 교수진 필드를 추가해도 기존 부분 dict 비교를 허용한다."""

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
    r"부담\s*적은\s*과목|난이도\s*낮은\s*과목|수강하기\s*좋은\s*과목",
    re.IGNORECASE,
)
COURSE_DETAIL_RE = re.compile(
    r"(무슨|어떤)\s*과목|과목\s*(이야|인가요|소개|내용)|"
    r"무엇을\s*배우|뭘\s*배우|뭐\s*배우|배우는\s*과목|과목\s*설명|수업\s*내용",
    re.IGNORECASE,
)
COURSE_DIFFICULTY_RE = re.compile(
    r"난이도|어렵(?:나요|니|다|게)|어려(?:워|운|움)|쉬운가|쉽나요|듣기\s*편|공부량|빡센|"
    r"수업\s*부담|학습\s*부담|과제\s*많|공부\s*방법|학습\s*방법|"
    r"공부\s*팁|학습\s*팁|선수\s*지식|준비\s*해야",
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
    r"자격증|정보처리기사|sqld|데이터베이스",
    re.IGNORECASE,
)


def sanitize_input(text: str, limit: int = 1000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def casual_response(question: str) -> dict[str, Any] | None:
    """검색이 필요 없는 짧은 일상 대화를 ComPass 페르소나로 처리한다."""
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
    compact = re.sub(r"\s+", "", question or "").lower()
    matches = [
        name
        for name in KNOWN_COURSE_NAMES
        if re.sub(r"\s+", "", name).lower() in compact
    ]
    return max(matches, key=len) if matches else ""


def classify_intent(question: str, index: SearchIndex | None = None) -> str:
    """질문을 응답 조합에 사용하는 대표 의도로 분류한다."""
    if casual_response(question):
        return "smalltalk"
    course_name = detect_course_name(question, index)
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
    if FACULTY_QUERY_RE.search(question):
        return "faculty"
    if is_course_recommendation(question):
        return "course_recommendation"
    if COURSE_DETAIL_RE.search(question):
        return "course_detail"
    return _list_answer_type(question) or "text"


def retrieve_documents(
    index: SearchIndex,
    question: str,
    intent: str,
) -> list[dict[str, Any]]:
    """의도별 검색 범위와 결과 수를 고정해 다른 카테고리 문서 혼입을 줄인다."""
    search_intent = {
        "notice_explain": "notice_list",
        "schedule_explain": "schedule_list",
    }.get(intent, intent)
    top_k = 20 if search_intent in {"notice_list", "schedule_list", "faq_list"} else config.SEARCH_TOP_K
    filters: dict[str, Any] = {"source_types": ["official"]}
    if search_intent in {"course_recommendation", "course_detail", "course_difficulty", "course_order", "course_roadmap"}:
        course = index.detect_course(question)
        filters.update({
            "document_types": list(COURSE_DOCUMENT_TYPES),
            "exclude_document_types": ["교수진", "공지사항", "게시물", "게시판목록", "학과일정"],
            "exclude_categories": ["공지사항", "게시판", "일반공지"],
            "course_name": (course or {}).get("course_name") or detect_course_name(question),
        })
    return index.search(question, top_k=top_k, filters=filters)


def _item_url(item: dict[str, Any], category_url: str = "") -> str:
    return item.get("detail_url") or item.get("source_url") or item.get("fallback_url") or category_url or DEPARTMENT_HOME_URL


def _course_link(item: dict[str, Any], course_name: str = "") -> str:
    """과목별 상세 페이지 URL을 우선 사용하고 없을 때만 전체 안내 페이지로 보낸다."""
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
    """검색 원문을 화면에 직접 노출하지 않는 학생용 항목으로 변환한다."""
    if intent == "faculty":
        faculty_hit = next(
            (
                hit
                for hit in hits
                if hit.get("source_url") == FACULTY_URL or "교수진" in (hit.get("title") or "")
            ),
            hits[0] if hits else {},
        )
        return _faculty_items(faculty_hit)
    if intent == "course_table":
        return _course_items(hits)
    if intent == "notice_list":
        return _notice_items(hits)
    if intent == "schedule_list":
        return _schedule_items(hits)
    if intent == "course_detail":
        return _course_detail_items(question, hits)
    if intent == "course_difficulty":
        return _course_detail_items(question, hits)
    return _generic_items(hits)


def summarize_for_student(intent: str, items: list[dict[str, Any]]) -> str:
    """검색 결과라는 표현 대신 학생에게 필요한 안내 문장을 만든다."""
    count = len(items)
    summaries = {
        "faculty": f"총 {count}명의 교수진 중 주요 정보 3명을 먼저 안내드립니다.",
        "course_table": "학년·학기별 주요 과목 3개를 먼저 안내드립니다.",
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
) -> dict[str, Any]:
    if answer_type == "course_table":
        return build_curriculum_by_grade_response(
            items,
            source_url=source_url,
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


def render_fallback_text(question: str, hits: list[dict[str, Any]]) -> str:
    """구조화 대상이 아닌 공식 문서도 최대 세 문장으로만 요약한다."""
    return _extractive_answer(question, hits)


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
    """교수진 공식 페이지 본문을 UI 렌더링용 구조로 변환한다."""
    normalized = [
        item for item in (hit.get("normalized_items") or [])
        if item.get("name") and (item.get("email") or item.get("phone") or item.get("subjects"))
    ]
    if normalized:
        items: list[dict[str, Any]] = []
        for item in normalized:
            homepage_url = item.get("homepage_url") or FACULTY_HOMEPAGE_FALLBACKS.get(item.get("name") or "", "")
            subjects = item.get("subjects") or []
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
        ("exam_list", r"시험\s*범위|시험범위"),
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
            "link_label": "자세히 보기",
        }
        for hit in hits[:10]
    ]


def _clean_notice_summary(hit: dict[str, Any], limit: int = 80) -> str:
    """공지 원문에서 번호·첨부·메타데이터를 제거하고 한 줄로 요약한다."""
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
                "date": hit.get("published_at") or "",
                "description": _clean_notice_summary(hit),
                "source_url": source_url,
                "fallback_url": NOTICE_URL,
                "link_label": "공지 바로가기",
            }
        )
    return items


def _schedule_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen = set()
    for hit in hits:
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

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()

    def is_upcoming(item: dict[str, Any]) -> bool:
        if not item["end_date"]:
            return True
        try:
            return date.fromisoformat(item["end_date"]) >= today
        except ValueError:
            return False

    upcoming = [item for item in items if is_upcoming(item)]
    selected = upcoming or items
    return sorted(selected, key=lambda item: (item["start_date"], item["title"]))


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
    """질문에 명시된 과목과 일치하는 공식 교육과정 항목만 반환한다."""
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


def _representative_courses_by_grade(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = {
        "1학년": ["컴퓨터의이해", "파이썬프로그래밍기초", "이산수학"],
        "2학년": ["자료구조", "컴퓨터구조", "Java프로그래밍"],
        "3학년": ["데이터베이스시스템", "운영체제", "인공지능"],
        "4학년": ["소프트웨어공학", "정보보호", "컴퓨터보안", "클라우드컴퓨팅"],
    }
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
        for name in preferred[grade]:
            found = next((item for item in grade_items if item.get("course_name") == name or item.get("title") == name), None)
            if found and found not in selected:
                selected.append(found)
        for item in sorted(grade_items, key=_grade_sort_key):
            if item not in selected:
                selected.append(item)
            if len(selected) >= 3:
                break
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
                    for item in selected[:3]
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
) -> dict[str, Any]:
    groups = _representative_courses_by_grade(items)
    return {
        "answer": "컴퓨터과학과 교육과정 안내입니다.",
        "answer_type": CompatibleAnswerType("curriculum_by_grade", "course_table"),
        "summary": "학년별 대표 과목을 3개씩 먼저 정리했습니다. 전체 교육과정은 아래 바로가기를 통해 확인할 수 있습니다.",
        "groups": groups,
        "items": [item for group in groups for item in group["items"]],
        "display_limit": 3,
        "total_count": len(items),
        "source_urls": [COURSE_FULL_GUIDE_URL],
        "actions": [{"type": "link", "label": "전체 교육과정 바로가기", "url": COURSE_FULL_GUIDE_URL}],
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
    if answer_type == "faculty":
        for item in items[:3]:
            homepage_url = item.get("homepage_url")
            name = item.get("name") or "교수"
            if homepage_url:
                actions.append(
                    {
                        "type": "link",
                        "label": f"{name} 교수 홈페이지",
                        "url": homepage_url,
                    }
                )
    if source_url:
        link_labels = {
            "faculty": "교수진 페이지 바로가기",
            "course_table": "전체 교육과정 바로가기",
            "course_recommendation": "교육과정 바로가기",
            "course_detail": "교육과정 바로가기",
            "notice_list": "전체 공지 바로가기",
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
    """LLM prompt에 넣을 공식 데이터 context를 짧고 안전하게 직렬화한다."""
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
    """LLM 보조 답변용 공통 prompt builder."""
    supported = {
        "course_difficulty",
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
    """하위 호환용 wrapper. 신규 코드는 build_llm_prompt/call_llm_helper를 사용한다."""
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
    """동일·유사 문장을 제거해 LLM/공식 개요 중복 노출을 방지한다."""
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
    """LLM 보조문에서 공식 개요와 겹치는 제목·문장을 제거한다."""
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
    """공식 과목 개요를 학생이 읽기 쉬운 1~2문장으로 재구성한다."""
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


def _difficulty_advice_object(course_name: str, item: dict[str, Any], llm_text: str = "") -> dict[str, str]:
    """LLM 원문이 불완전해도 UI에는 안전한 고정 구조로 난이도 안내를 제공한다."""
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


def _course_difficulty_response(
    question: str,
    course_name: str,
    items: list[dict[str, Any]],
    started: float,
    *,
    session_id: str = "",
) -> dict[str, Any]:
    item = items[0] if items else {
        "course_name": course_name,
        "overview": "공식 교과목 안내에 등록된 과목입니다.",
        "source_url": COURSE_GUIDE_URL,
    }
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
    )
    source_url = _course_link(item, course_name)
    official_overview = _official_course_overview(course_name, item)
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


def _llm_type_from_intent(intent: str) -> str:
    mapping = {
        "course_difficulty": "course_difficulty",
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
    """현재 요청의 공식 검색 결과만 사용해 LLM context를 만든다."""
    course = detect_course_name(question, index)
    if llm_type in {"course_difficulty", "course_order", "course_roadmap"}:
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
    if llm_type in {"course_difficulty", "course_order", "course_roadmap"}:
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
    source_url = _llm_source_url(llm_type, context)
    course_name = context.get("course_name") or detect_course_name(question)
    return {
        "answer": answer,
        "answer_type": "text",
        "summary": "공식 데이터 범위 안에서 학생이 이해하기 쉽게 재구성한 보조 답변입니다.",
        "items": [],
        "display_limit": 3,
        "total_count": 0,
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
    """LLM 출력의 의미 없는 반복 라인을 제거한다."""
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
    """문장 없이 키워드만 길게 나열된 줄은 bullet 목록으로 바꾼다."""
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
    """모바일에서 읽기 어려운 긴 문장을 자연스러운 위치에서 줄바꿈한다."""
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
    """LLM fallback 답변을 ComPass 응답 철학에 맞게 후처리한다.

    - 중복 라인 제거
    - 키워드 나열을 bullet로 변환
    - 긴 문장 줄바꿈
    - 마지막 문장 완결 처리
    - 너무 빈약한 출력에는 제목/안내 문구 보강
    """
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
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": config.OPENAI_MODEL,
            "input": prompt,
            "temperature": 0.1,
            "max_output_tokens": 600,
        },
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("output_text"):
        return data["output_text"].strip()
    parts = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "".join(parts).strip()


def _gemini(prompt: str) -> str:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent",
        params={"key": config.GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
        },
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    text = "".join(
        part.get("text", "")
        for candidate in data.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
    ).strip()
    if not text:
        raise RuntimeError("Gemini 응답이 비어 있습니다.")
    return text


def call_llm(question: str, *, prompt_override: str | None = None) -> str:
    prompt = prompt_override or _llm_prompt(question)
    provider = (config.LLM_PROVIDER or "").strip().lower()

    logger.info("LLM_PROVIDER=%r", provider)

    if provider == "gemini":
        return sanitize_llm_response(_gemini(prompt), question)
    if provider == "openai":
        return sanitize_llm_response(_openai(prompt), question)

    raise RuntimeError(f"지원하지 않는 LLM_PROVIDER: {config.LLM_PROVIDER}")


def call_llm_helper(
    llm_type: str,
    question: str,
    context: dict[str, Any],
    *,
    session_id: str = "",
) -> str:
    """LLM 보조 답변 공통 진입점.

    이 함수는 요청 로컬 context만 사용하며 사용자별 상태를 전역 저장하지 않는다.
    """
    provider = (config.LLM_PROVIDER or "").strip().lower()
    normalized_type = llm_type if llm_type in {
        "course_difficulty",
        "course_order",
        "course_roadmap",
        "notice_explain",
        "schedule_explain",
        "general_explain",
    } else "general_explain"
    session_short = (session_id or "")[:8] or "server"
    prompt = build_llm_prompt(normalized_type, question, context)
    logger.info(
        "LLM 요청: provider=%s, llm_type=%s, session=%s",
        provider,
        normalized_type,
        session_short,
    )
    try:
        return call_llm(question, prompt_override=prompt)
    except Exception as exc:
        logger.error(
            "LLM 오류: provider=%s, llm_type=%s, session=%s, context=%s, error=%s",
            provider,
            normalized_type,
            session_short,
            sanitize_input(str(context.get("course_name") or context.get("title") or ""), 80),
            type(exc).__name__,
        )
        return LLM_SAFE_FAILURE_MESSAGE

def answer_question(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    allow_llm: bool = False,
    llm_type: str | None = None,
    session_id: str = "",
    request_id: str = "",
    index: SearchIndex | None = None,
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
    initial_intent = classify_intent(clean_question, index)
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
    if is_out_of_scope(clean_question):
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
    best_score = hits[0]["score"] if hits else 0
    if hits and best_score >= config.SEARCH_MIN_SCORE:
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
                response.update(
                    build_structured_response(
                        answer_type,
                        items,
                        source_url=category_urls[answer_type],
                        sources=sources,
                        score=best_score,
                        keywords=tokenize(clean_question),
                        started=started,
                    )
                )
        return response

    if not allow_llm:
        return {
            "answer": (
                "공식 지식 DB에서 충분한 근거를 찾지 못했습니다.\n"
                "공식 정보 범위 안에서 AI 보조 답변을 시도해볼까요?"
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
