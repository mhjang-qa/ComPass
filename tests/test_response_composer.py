from chatbot import (
    build_structured_response,
    classify_intent,
    normalize_results,
    summarize_for_student,
)


def test_response_composer_classifies_supported_intents() -> None:
    assert classify_intent("컴퓨터과학과 교수진 정보를 알려줘") == "faculty"
    assert classify_intent("컴퓨터과학과 교육과정을 알려줘") == "course_table"
    assert classify_intent("컴퓨터과학과 최근 공지를 알려줘") == "notice_list"
    assert classify_intent("컴퓨터과학과 학과 일정을 알려줘") == "schedule_list"
    assert classify_intent("편입생 과목 추천") == "course_recommendation"
    assert classify_intent("인공지능은 무슨 과목이야?") == "course_detail"
    assert classify_intent("인공지능은 어떤 커리큘럼이야?") == "course_detail"
    assert classify_intent("파이썬프로그래밍기초 수업 난이도는?") == "course_difficulty"
    assert classify_intent("운영체제는 어려워?") == "course_difficulty"
    assert classify_intent("컴퓨터구조는 뭐 배우는 과목이야?") == "course_detail"


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
        "chatbot.call_llm",
        lambda question, **kwargs: "참고용 체감 난이도는 보통이며 프로세스 개념을 먼저 복습하세요.",
    )

    result = answer_question("운영체제는 어려워?", allow_llm=True, index=index)

    assert result["answer_type"] == "course_difficulty"
    assert result["mode"] == "LLM"
    assert "운영체제의 구조" in result["items"][0]["official_overview"]
    assert "참고용" in result["items"][0]["difficulty_advice"]
    assert "공식 기준이 아닌 참고용" in result["items"][0]["disclaimer"]
    assert result["items"][0]["source_url"].endswith("course-34416")
