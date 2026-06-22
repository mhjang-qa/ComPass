from chatbot import answer_question
from search_index import SearchIndex


def empty_index(tmp_path) -> SearchIndex:
    return SearchIndex(tmp_path / "empty-index.json")


def test_graduation_credits_answer(tmp_path) -> None:
    result = answer_question("졸업하려면 몇 학점 필요해?", index=empty_index(tmp_path))

    assert result["mode"] == "DB검색"
    assert result["answer"] == "졸업하려면 총 130학점 이상이 필요합니다."
    assert result["structured_intent"] == "graduation_requirement"


def test_recommended_certifications_follow_up(tmp_path) -> None:
    result = answer_question(
        "추천 자격증은?",
        history=[
            {"role": "user", "content": "컴퓨터과학과 진로를 준비하고 있어"},
            {"role": "assistant", "content": "어떤 정보가 필요하신가요?"},
        ],
        index=empty_index(tmp_path),
    )

    assert result["answer"] == "추천 자격증은 정보처리기사와 SQLD입니다."
    assert result["structured_intent"] == "career_certification"


def test_database_exam_scope_is_subject_specific(tmp_path) -> None:
    result = answer_question("데이터베이스 시험 범위는?", index=empty_index(tmp_path))
    other = answer_question("운영체제 시험 범위는?", index=empty_index(tmp_path))

    assert result["answer"] == "데이터베이스 시험 범위는 13~15장입니다."
    assert result["structured_intent"] == "exam_scope"
    assert other.get("structured_intent") is None
