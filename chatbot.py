"""Notion DB 우선 RAG 챗봇과 제한적 LLM fallback."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

import config
from curated_knowledge import match_curated
from search_index import SearchIndex, tokenize
from search_index import FACULTY_QUERY_RE, FACULTY_URL

logger = logging.getLogger(__name__)

OUT_OF_SCOPE_MESSAGE = (
    "죄송합니다. 해당 내용은 한국방송통신대학교 컴퓨터과학과 공식 데이터에서 확인되지 않습니다. "
    "컴퓨터과학과 홈페이지에 등록된 공식 정보 기준으로만 안내할 수 있습니다."
)
GREETING_MESSAGE = (
    "안녕하세요 👋\n"
    "저는 한국방송통신대학교 컴퓨터과학과 학생들의 길잡이, ComPass입니다.\n"
    "공지사항, 교육과정, 교수진, 졸업요건, 학사일정 같은 공식 정보를 안내해 드릴 수 있습니다."
)
IDENTITY_MESSAGE = (
    "저는 ComPass입니다.\n"
    "Computer Science와 Compass(나침반)를 결합한 이름으로, "
    "한국방송통신대학교 컴퓨터과학과 학생들이 필요한 공식 정보를 쉽게 찾도록 돕는 "
    "RAG 기반 학과 안내 챗봇입니다."
)
CAPABILITY_MESSAGE = (
    "ComPass는 컴퓨터과학과 공식 홈페이지 정보를 기반으로 다음 내용을 안내할 수 있습니다.\n\n"
    "- 공지사항\n- 교육과정\n- 교수진\n- 학사일정\n- 졸업요건\n- FAQ\n- 추천 자격증\n- 시험범위\n\n"
    "궁금한 내용을 자연스럽게 질문해 주세요."
)
THANKS_MESSAGE = (
    "도움이 되었다니 다행입니다 😊\n"
    "컴퓨터과학과 관련해서 더 궁금한 내용이 있으면 언제든지 질문해 주세요."
)
CASUAL_LIMIT_MESSAGE = (
    "저는 일상 대화보다는 한국방송통신대학교 컴퓨터과학과 공식 정보를 안내하는 역할에 집중하고 있습니다.\n"
    "컴퓨터과학과 교육과정, 교수진, 졸업요건, 학사일정 등이 궁금하시면 바로 안내해 드릴게요."
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
        if len(selected) >= 5:
            break
    if not selected:
        selected = [hit.get("summary", "") for hit in hits[:2] if hit.get("summary")]
    return "\n".join(f"- {sentence[:500]}" for sentence in selected) or OUT_OF_SCOPE_MESSAGE


def _faculty_items(hit: dict[str, Any]) -> list[dict[str, Any]]:
    """교수진 공식 페이지 본문을 UI 렌더링용 구조로 변환한다."""
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

        items.append(
            {
                "name": name,
                "title": title_match.group(1) if title_match else "교수",
                "email": email_match.group(1).strip(".,") if email_match else "",
                "phone": phone_match.group(1) if phone_match else "",
                "subjects_undergraduate": subjects(undergraduate_match),
                "subjects_graduate": subjects(graduate_match),
                "source_url": hit.get("source_url") or FACULTY_URL,
            }
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
        ("notice", r"최근\s*공지|공지사항|학과\s*공지"),
        ("curriculum", r"교육과정|교과과정|커리큘럼"),
        ("schedule", r"학과\s*일정|학사\s*일정"),
        ("faq", r"faq|자주\s*묻는\s*질문"),
        ("certification", r"추천\s*자격증|자격증\s*추천"),
        ("exam", r"시험\s*범위|시험범위"),
    )
    for answer_type, pattern in patterns:
        if re.search(pattern, question, re.IGNORECASE):
            return answer_type
    return ""


def _generic_items(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": hit.get("title") or "공식 정보",
            "summary": hit.get("summary") or hit.get("body") or "",
            "category": hit.get("category") or "",
            "published_at": hit.get("published_at") or "",
            "source_url": hit.get("source_url") or "",
        }
        for hit in hits[:10]
    ]


def _llm_prompt(question: str) -> str:
    return f"""
너는 한국방송통신대학교 컴퓨터과학과 공식 정보만 안내하는 챗봇 'ComPass'다.
질문에 답할 때 다음 규칙을 반드시 지켜라.
1. 컴퓨터과학과 공식 정보 범위를 벗어나면 정확히 다음 문장만 답한다:
{OUT_OF_SCOPE_MESSAGE}
2. 확실하지 않거나 최신 공식 정보 확인이 필요한 내용은 추측하지 말고 위 거절 문장을 답한다.
3. 존재를 확신하지 못하는 날짜, 규정, 사람, URL을 만들지 않는다.
4. 일반 지식이나 개인 조언으로 답변 범위를 넓히지 않는다.
5. 답변은 한국어로 간결하게 작성한다.

사용자 질문: {question}
""".strip()


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
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 600},
        },
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()
    return "".join(
        part.get("text", "")
        for candidate in data.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
    ).strip()


def call_llm(question: str) -> str:
    prompt = _llm_prompt(question)
    if config.LLM_PROVIDER == "gemini":
        return _gemini(prompt)
    if config.LLM_PROVIDER == "openai":
        return _openai(prompt)
    raise RuntimeError(f"지원하지 않는 LLM_PROVIDER: {config.LLM_PROVIDER}")


def answer_question(
    question: str,
    *,
    history: list[dict[str, str]] | None = None,
    allow_llm: bool = False,
    index: SearchIndex | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    clean_question = sanitize_input(question)
    if not clean_question:
        return {"answer": "질문을 입력해 주세요.", "mode": "SYSTEM", "sources": [], "score": 0}
    casual = casual_response(clean_question)
    if casual:
        casual["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return casual
    curated = match_curated(clean_question, history)
    if curated:
        return {
            "answer": curated["answer"],
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
    if is_out_of_scope(clean_question):
        return {
            "answer": OUT_OF_SCOPE_MESSAGE,
            "mode": "SYSTEM",
            "sources": [],
            "score": 0,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": "범위 외 질문",
        }

    search_question = contextualize(clean_question, history)
    index = index or SearchIndex()
    hits = index.search(search_question)
    best_score = hits[0]["score"] if hits else 0
    if hits and best_score >= config.SEARCH_MIN_SCORE:
        sources = [
            {"title": hit.get("title"), "url": hit.get("source_url"), "score": hit.get("score")}
            for hit in hits[:3]
            if hit.get("source_url")
        ]
        response = {
            "answer": _extractive_answer(search_question, hits),
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
            items = _faculty_items(faculty_hit)
            if items:
                response.update(
                    answer="컴퓨터과학과 교수진 정보입니다.",
                    answer_type="faculty",
                    items=items,
                    total_count=len(items),
                    source_urls=[faculty_hit.get("source_url") or FACULTY_URL],
                )
        else:
            answer_type = _list_answer_type(search_question)
            if answer_type:
                items = _generic_items(hits)
                response.update(
                    answer_type=answer_type,
                    items=items,
                    total_count=len(items),
                    source_urls=[source["url"] for source in sources],
                )
        return response

    if not allow_llm:
        return {
            "answer": (
                "공식 지식 DB에서 충분한 근거를 찾지 못했습니다. "
                "제한된 범위에서 LLM 보조 검색을 진행할까요?"
            ),
            "mode": "LLM확인",
            "requires_llm_confirmation": True,
            "sources": [],
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": "검색 점수 기준 미달",
        }

    try:
        answer = call_llm(clean_question)
        if not answer:
            answer = OUT_OF_SCOPE_MESSAGE
        return {
            "answer": answer,
            "mode": "LLM",
            "sources": [],
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        logger.exception("LLM fallback 실패: %s", exc)
        return {
            "answer": OUT_OF_SCOPE_MESSAGE,
            "mode": "LLM",
            "sources": [],
            "score": best_score,
            "keywords": tokenize(clean_question),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "failure_reason": f"LLM 호출 실패: {type(exc).__name__}",
        }
