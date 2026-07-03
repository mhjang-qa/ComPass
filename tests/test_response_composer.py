from chatbot import (
    analyze_question_intent,
    build_structured_response,
    classify_intent,
    normalize_results,
    summarize_for_student,
)


def test_response_composer_classifies_supported_intents() -> None:
    assert classify_intent("컴퓨터과학과 교수진 정보를 알려줘") == "faculty"
    assert classify_intent("컴퓨터 과학과 교수진") == "faculty"
    assert classify_intent("교수진") == "faculty"
    assert classify_intent("교수") == "faculty"
    assert classify_intent("컴퓨터과학과 교수") == "faculty"
    assert classify_intent("컴퓨터 과학과 교수님") == "faculty"
    assert classify_intent("컴퓨터과학과 교육과정을 알려줘") == "course_table"
    assert classify_intent("컴퓨터과학과 최근 공지를 알려줘") == "notice_list"
    assert classify_intent("컴퓨터과학과 학과 일정을 알려줘") == "schedule_list"
    assert classify_intent("편입생 과목 추천") == "course_recommendation"
    assert classify_intent("인공지능은 무슨 과목이야?") == "course_detail"
    assert classify_intent("인공지능은 어떤 커리큘럼이야?") == "course_detail"
    assert classify_intent("파이썬프로그래밍기초 수업 난이도는?") == "course_difficulty"
    assert classify_intent("운영체제는 어려워?") == "course_difficulty"
    assert classify_intent("컴퓨터구조는 뭐 배우는 과목이야?") == "course_detail"
    assert classify_intent("데이터베이스 시험범위는?") == "exam_scope"
    assert classify_intent("운영체제 기말 범위") == "exam_scope"


def test_nlu_router_detects_professor_detail_before_search() -> None:
    routed = analyze_question_intent("손진곤 교수")

    assert routed["intent"] == "faculty_detail"
    assert routed["entities"]["faculty_name"] == "손진곤"
    assert routed["search_scope"] == ["faculty"]


def test_nlu_router_classifies_natural_language_intents() -> None:
    cases = {
        "컴퓨터 과학과 교수": "faculty_list",
        "교수님 알려줘": "faculty_list",
        "손진곤 교수 이메일": "faculty_detail",
        "인공지능이 뭐야": "course_detail",
        "인공지능 어렵나요": "course_difficulty",
        "인공지능 C이상 맞으려면": "course_grade_strategy",
        "데이터베이스시스템 듣기 전에 뭐 알아야 해": "course_order",
        "편입생인데 어떤 과목부터 들어": "course_roadmap",
        "최근 공지": "recent_notice",
        "학과에서 새로 올라온 공지 있어?": "recent_notice",
        "학과 일정": "schedule",
        "시험 일정": "schedule",
        "인공지능 시험 어디까지": "exam_scope",
        "졸업하려면 몇 학점": "graduation",
        "편입 안내": "course_roadmap",
        "장학금 안내": "scholarship",
        "학과 전화번호": "contact",
    }

    for question, expected in cases.items():
        assert analyze_question_intent(question)["intent"] == expected


def test_nlu_router_extracts_course_and_goal_entities() -> None:
    routed = analyze_question_intent("인공지능 C이상 맞으려면")

    assert routed["intent"] == "course_grade_strategy"
    assert routed["entities"]["course_name"] == "인공지능"
    assert routed["entities"]["grade_goal"] == "C 이상"


def test_ai_course_detail_is_student_friendly_and_does_not_mix_documents(tmp_path) -> None:
    from chatbot import answer_question
    from search_index import SearchIndex

    result = answer_question(
        "인공지능은 무슨 과목이야?",
        index=SearchIndex(tmp_path / "empty.json"),
    )

    assert result["answer_type"] == "course_detail"
    assert result["answer"] == "인공지능 과목 안내입니다."
    assert result["total_count"] == 1
    assert result["items"][0]["title"] == "인공지능"
    assert "탐색 알고리즘" in result["items"][0]["topics"]
    assert result["items"][0]["link_label"] == "인공지능 과목 바로가기"
    combined = str(result)
    assert "교수 이메일" not in combined
    assert "경진대회" not in combined
    assert "글번호" not in combined


def test_course_normalization_uses_student_friendly_feature_and_link() -> None:
    hits = [
        {
            "source_url": "https://cs.knou.ac.kr/cs1/4789/subview.do",
            "normalized_items": [
                {
                    "course_name": "컴퓨터의이해",
                    "grade": "1학년",
                    "semester": "1학기",
                    "category": "전공",
                    "course_code": "34172",
                    "media": ["웹강의"],
                    "evaluation": ["기말평가"],
                }
            ],
        }
    ]

    items = normalize_results("course_table", hits)

    assert items[0]["feature"] == "전공의 기본 개념을 익히는 입문 과목입니다."
    assert items[0]["source_url"].endswith("/4789/subview.do")
    assert items[0]["link_label"] == "교육과정 바로가기"


def test_structured_response_limits_initial_display_and_keeps_item_urls() -> None:
    items = [
        {
            "title": f"공지 {index}",
            "source_url": f"https://cs.knou.ac.kr/notice/{index}",
        }
        for index in range(5)
    ]

    response = build_structured_response(
        "notice_list",
        items,
        source_url="https://cs.knou.ac.kr/cs1/4812/subview.do",
        sources=[],
        score=95,
        keywords=["공지"],
        started=0,
    )

    assert response["display_limit"] == 3
    assert response["total_count"] == 5
    assert len(response["source_urls"]) == 5
    assert "3개" in summarize_for_student("notice_list", items)
    assert response["actions"][0]["type"] == "expand"
    assert response["actions"][-1]["type"] == "link"


def test_course_difficulty_requests_llm_confirmation_without_rejecting(tmp_path) -> None:
    from chatbot import answer_question
    from search_index import SearchIndex

    index = SearchIndex(tmp_path / "course-index.json")
    index.rebuild(
        [
            {
                "title": "파이썬프로그래밍기초",
                "category": "교과정보 > 교과목안내 > 과목상세",
                "document_type": "과목상세",
                "body": "파이썬 기초 문법과 프로그램 작성 방법을 학습한다.",
                "summary": "파이썬 프로그래밍 입문",
                "source_url": "https://cs.knou.ac.kr/cs1/4791/subview.do#course-34174",
                "normalized_items": [
                    {
                        "course_name": "파이썬프로그래밍기초",
                        "overview": "파이썬 기초 문법과 프로그램 작성 방법을 학습하는 과목입니다.",
                        "topics": ["변수", "조건문", "반복문"],
                    }
                ],
            }
        ]
    )

    result = answer_question("파이썬프로그래밍기초 수업 난이도는?", index=index)

    assert result["answer_type"] == "llm_confirmation_required"
    assert result["requires_llm_confirmation"] is True
    assert result["course_name"] == "파이썬프로그래밍기초"
    assert "LLM 보조 답변을 사용할까요?" in result["answer"]
    assert result["actions"][0]["type"] == "confirm_llm"


def test_course_difficulty_llm_answer_separates_official_and_advice(tmp_path, monkeypatch) -> None:
    from chatbot import answer_question
    from search_index import SearchIndex

    index = SearchIndex(tmp_path / "course-index.json")
    index.rebuild(
        [
            {
                "title": "운영체제",
                "category": "교과정보 > 교과목안내 > 과목상세",
                "document_type": "과목상세",
                "body": "운영체제의 구조와 프로세스 관리 원리를 학습한다.",
                "summary": "운영체제 공식 과목 정보",
                "source_url": "https://cs.knou.ac.kr/cs1/4791/subview.do#course-34416",
                "normalized_items": [
                    {
                        "course_name": "운영체제",
                        "overview": "운영체제의 구조와 프로세스 관리 원리를 학습하는 과목입니다.",
                        "topics": ["프로세스", "메모리 관리", "파일 시스템"],
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(
        "chatbot.call_llm_raw",
        lambda prompt: (
            "체감 난이도: 참고용으로는 보통 수준입니다.\n"
            "어렵게 느껴질 수 있는 부분: 프로세스와 메모리 관리 개념입니다.\n"
            "필요한 준비: 운영체제 기본 용어를 먼저 정리하세요.\n"
            "학습 팁: 프로세스 개념을 먼저 복습하세요."
        ),
    )

    result = answer_question("운영체제는 어려워?", allow_llm=True, index=index)

    assert result["answer_type"] == "course_difficulty"
    assert result["mode"] == "LLM"
    assert "운영체제의 구조" in result["items"][0]["official_overview"]
    assert "참고용" in result["items"][0]["difficulty_advice"]["체감 난이도"]
    assert "공식 기준이 아닌 참고용" in result["items"][0]["disclaimer"]
    assert result["items"][0]["source_url"].endswith("course-34416")


def test_course_difficulty_llm_key_missing_returns_structured_failure(tmp_path, monkeypatch) -> None:
    import config
    from chatbot import answer_question
    from search_index import SearchIndex

    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    index = SearchIndex(tmp_path / "course-index.json")
    index.rebuild(
        [
            {
                "title": "인공지능",
                "category": "교과정보 > 교과목안내 > 과목상세",
                "document_type": "과목상세",
                "body": "인공지능의 기본 이론과 탐색 및 추론을 학습한다.",
                "source_url": "https://cs.knou.ac.kr/cs1/4791/subview.do#course-34524",
                "normalized_items": [
                    {
                        "course_name": "인공지능",
                        "overview": "인공지능의 기본 이론과 탐색 및 추론을 학습하는 과목입니다.",
                        "topics": ["탐색", "추론", "학습"],
                    }
                ],
            }
        ]
    )

    result = answer_question("인공지능 과목은 많이 어려워?", allow_llm=True, index=index, request_id="req-key")

    assert result["ok"] is False
    assert result["answer_type"] == "llm_fallback_failed"
    assert result["error_code"] == "LLM_API_KEY_MISSING"
    assert result["fallback_available"] is True
    assert "현재 LLM 보조 답변을 불러오지 못했습니다" in result["user_message"]
    assert "인공지능" in result["items"][0]["official_overview"]


def test_course_difficulty_llm_timeout_returns_structured_failure(tmp_path, monkeypatch) -> None:
    import config
    import requests
    from chatbot import answer_question
    from search_index import SearchIndex

    def timeout_post(*args, **kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setattr("chatbot.requests.post", timeout_post)
    index = SearchIndex(tmp_path / "course-index.json")
    index.rebuild(
        [
            {
                "title": "인공지능",
                "category": "교과정보 > 교과목안내 > 과목상세",
                "document_type": "과목상세",
                "body": "인공지능의 기본 이론과 탐색 및 추론을 학습한다.",
                "source_url": "https://cs.knou.ac.kr/cs1/4791/subview.do#course-34524",
                "normalized_items": [
                    {
                        "course_name": "인공지능",
                        "overview": "인공지능의 기본 이론과 탐색 및 추론을 학습하는 과목입니다.",
                        "topics": ["탐색", "추론", "학습"],
                    }
                ],
            }
        ]
    )

    result = answer_question("인공지능 과목은 많이 어려워?", allow_llm=True, index=index, request_id="req-timeout")

    assert result["answer_type"] == "llm_fallback_failed"
    assert result["error_code"] == "LLM_TIMEOUT"


def test_gemini_rate_limit_tries_configured_fallback_model(monkeypatch) -> None:
    import config
    import requests
    import chatbot

    calls: list[str] = []

    class FakeResponse:
        def __init__(self, status_code: int, text: str = "") -> None:
            self.status_code = status_code
            self._text = text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                error = requests.HTTPError(f"HTTP {self.status_code}")
                error.response = self
                raise error

        def json(self) -> dict:
            return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}

    def fake_post(url, **kwargs):
        model = url.split("/models/", 1)[1].split(":generateContent", 1)[0]
        calls.append(model)
        if model == "gemini-2.5-flash":
            return FakeResponse(429)
        return FakeResponse(200, "fallback model answer")

    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", ["gemini-2.0-flash"])
    monkeypatch.setattr("chatbot.requests.post", fake_post)
    chatbot.LLM_COOLDOWN_UNTIL.clear()

    assert chatbot.call_llm_raw("hello") == "fallback model answer"
    assert calls == ["gemini-2.5-flash", "gemini-2.0-flash"]
    chatbot.LLM_COOLDOWN_UNTIL.clear()


def test_llm_intent_classifier_is_disabled_by_default(monkeypatch) -> None:
    import config
    import chatbot

    def fail_if_called(prompt):
        raise AssertionError("LLM intent classifier should not call provider by default")

    monkeypatch.setattr(config, "ENABLE_LLM_INTENT_CLASSIFIER", False)
    monkeypatch.setattr("chatbot.call_llm_raw", fail_if_called)

    assert chatbot.classify_intent_with_llm("인공지능 난이도") is None


def test_backend_localizes_dynamic_action_labels_to_english() -> None:
    from main import localize_response

    response = {
        "answer": "인공지능 학습 부담 안내입니다.",
        "answer_type": "course_difficulty",
        "summary": "요약",
        "actions": [
            {"type": "confirm_llm", "label": "LLM 보조 답변 사용"},
            {"type": "link", "label": "인공지능 과목 바로가기", "url": "https://example.com/ai"},
            {"type": "link", "label": "교과목 안내 바로가기", "url": "https://example.com/course"},
        ],
        "items": [{"title": "인공지능", "link_label": "인공지능 과목 바로가기"}],
    }

    localized = localize_response(response, "en")

    assert [action["label"] for action in localized["actions"]] == [
        "Use AI Helper",
        "View AI Course",
        "View Course Information",
    ]
    assert localized["items"][0]["link_label"] == "View AI Course"


def test_backend_localizes_notice_and_schedule_summaries_to_english() -> None:
    from main import localize_response

    notice = localize_response(
        {
            "answer": "컴퓨터과학과 최근 공지 안내입니다.",
            "answer_type": "notice_list",
            "summary": "컴퓨터과학과 최근 공지 3건을 안내드립니다.",
            "items": [{"title": "공지", "date": "2026-06-01"}],
            "actions": [{"type": "link", "label": "공지 더보기", "url": "https://example.com"}],
        },
        "en",
    )
    schedule_empty = localize_response(
        {
            "answer": "학과 일정 안내입니다.",
            "answer_type": "schedule_list",
            "summary": "현재 저장된 공식 데이터에서 학과 일정을 찾지 못했습니다.",
            "items": [],
            "actions": [{"type": "link", "label": "학과 일정 바로가기", "url": "https://example.com"}],
        },
        "en",
    )

    assert notice["summary"] == "Here are the 3 latest Computer Science department notices."
    assert notice["actions"][0]["label"] == "View More Notices"
    assert schedule_empty["summary"] == (
        "No schedule items were found in the saved official data.\n"
        "You can check the academic schedule on the official department page."
    )
    assert schedule_empty["actions"][0]["label"] == "View Schedule"
