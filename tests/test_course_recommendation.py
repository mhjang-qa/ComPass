from pathlib import Path

import main
from chatbot import answer_question
from search_index import SearchIndex


QUESTIONS = (
    "3학점 듣기편한과목은?",
    "3학년 편입생이 듣기 쉬운 과목 추천해줘",
    "직장인이 듣기 편한 과목 알려줘",
    "처음 수강하기 좋은 과목 알려줘",
    "쉬운 전공 과목 추천해줘",
)


def test_course_recommendation_intent_uses_curated_items(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "empty.json")
    index.rebuild([])

    for question in QUESTIONS:
        result = answer_question(question, index=index)
        assert result["answer_type"] == "course_recommendation"
        assert result["structured_intent"] == "transfer_student_course_recommendation"
        assert result["total_count"] >= 3
        assert result["display_limit"] == 3
        assert all(item.get("reason") for item in result["items"])
        assert all(item.get("difficulty_hint") for item in result["items"])
        assert all(item.get("workload_hint") for item in result["items"])
        assert not any(
            forbidden in result["answer"]
            for forbidden in ("재이수", "교재 출간", "첨부파일")
        )


def test_search_filters_exclude_notice_documents(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "filter.json")
    index.rebuild(
        [
            {
                "title": "쉬운 과목 추천 공지",
                "category": "공지사항",
                "document_type": "게시물",
                "body": "재이수 기준 변경 첨부파일 안내",
                "search_text": "쉬운 과목 추천 재이수 기준 변경",
            },
            {
                "title": "교육과정표",
                "category": "교육과정",
                "document_type": "교육과정표",
                "body": "컴퓨터의이해",
                "search_text": "쉬운 과목 추천 컴퓨터의이해",
                "normalized_items": [{"course_name": "컴퓨터의이해"}],
            },
        ]
    )

    hits = index.search(
        "쉬운 과목 추천",
        filters={
            "document_types": ["교육과정표", "검증지식"],
            "exclude_categories": ["공지사항", "게시판"],
        },
    )

    assert [hit["title"] for hit in hits] == ["교육과정표"]


def test_chat_route_handles_recommendation_before_index_loading(monkeypatch, tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "empty-route.json")
    index.rebuild([])
    monkeypatch.setattr(main, "index", index)
    monkeypatch.setattr(
        main,
        "ensure_search_index",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("추천 지식은 Notion 로딩 전에 처리되어야 합니다.")),
    )
    monkeypatch.setattr(main, "record_interaction_async", lambda *args, **kwargs: None)

    result = main.chat(main.ChatRequest(question="3학년 편입생 과목 추천"))

    assert result["answer_type"] == "course_recommendation"
