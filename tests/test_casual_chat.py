from pathlib import Path

import main
from chatbot import answer_question
from search_index import SearchIndex


def empty_index(tmp_path: Path) -> SearchIndex:
    index = SearchIndex(tmp_path / "empty-index.json")
    index.rebuild([])
    return index


def test_greeting_does_not_search_database(tmp_path: Path) -> None:
    result = answer_question("안녕", index=empty_index(tmp_path))

    assert result["mode"] == "일상대화"
    assert result["casual_intent"] == "greeting"
    assert "학생들의 길잡이, ComPass" in result["answer"]
    assert result["sources"] == []


def test_identity_question_uses_compass_persona(tmp_path: Path) -> None:
    result = answer_question("안녕, 넌 누구야?", index=empty_index(tmp_path))

    assert result["mode"] == "일상대화"
    assert result["casual_intent"] == "identity"
    assert "Computer Science" in result["answer"]
    assert "QA" not in result["answer"]
    assert "ChatGPT" not in result["answer"]


def test_capabilities_and_help_are_fixed_responses(tmp_path: Path) -> None:
    for question in ("뭐 할 수 있어?", "사용법 알려줘", "도움말"):
        result = answer_question(question, index=empty_index(tmp_path))
        assert result["mode"] == "일상대화"
        assert result["casual_intent"] == "capabilities"
        assert "공지사항" in result["answer"]
        assert "교수진" in result["answer"]


def test_thanks_and_casual_guardrail(tmp_path: Path) -> None:
    thanks = answer_question("고마워", index=empty_index(tmp_path))
    bored = answer_question("심심해", index=empty_index(tmp_path))

    assert thanks["casual_intent"] == "thanks"
    assert "다행입니다" in thanks["answer"]
    assert bored["casual_intent"] == "casual_guardrail"
    assert "공식 정보" in bored["answer"]


def test_out_of_scope_still_uses_guardrail(tmp_path: Path) -> None:
    result = answer_question("오늘 날씨 알려줘", index=empty_index(tmp_path))

    assert result["mode"] == "SYSTEM"
    assert result["failure_reason"] == "범위 외 질문"


def test_chat_route_handles_greeting_before_empty_index_loading(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "index", empty_index(tmp_path))
    monkeypatch.setattr(
        main,
        "ensure_search_index",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("일상 대화에서 DB를 로딩하면 안 됩니다.")),
    )
    monkeypatch.setattr(main, "record_interaction_async", lambda *args, **kwargs: None)

    result = main.chat(main.ChatRequest(question="안녕하세요"))

    assert result["mode"] == "일상대화"
    assert result["casual_intent"] == "greeting"
