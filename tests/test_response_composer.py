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
