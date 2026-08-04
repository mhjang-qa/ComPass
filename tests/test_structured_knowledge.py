from chatbot import answer_question
from search_index import SearchIndex


def empty_index(tmp_path) -> SearchIndex:
    return SearchIndex(tmp_path / "empty-index.json")


def test_graduation_credits_answer(tmp_path) -> None:
    result = answer_question("졸업하려면 몇 학점 필요해?", index=empty_index(tmp_path))

    assert result["mode"] == "DB검색"
    assert result["answer"] == "졸업하려면 총 130학점 이상이 필요합니다."
    assert result["structured_intent"] == "graduation_requirement"


def test_transfer_accepted_credits_answer(tmp_path) -> None:
    result = answer_question("편입생의 인정 학점은?", index=empty_index(tmp_path))

    assert result["mode"] == "DB검색"
    assert result["answer_type"] == "text"
    assert result["structured_intent"] == "transfer_accepted_credits"
    assert "2학년 편입 30학점" in result["answer"]
    assert "3학년 편입 63학점" in result["answer"]
    assert result["summary"]
    assert "과목" not in result["answer"]


def test_recommended_certifications_follow_up(tmp_path) -> None:
    result = answer_question(
        "추천 자격증은?",
        history=[
            {"role": "user", "content": "컴퓨터과학과 진로를 준비하고 있어"},
            {"role": "assistant", "content": "어떤 정보가 필요하신가요?"},
        ],
        index=empty_index(tmp_path),
    )

    assert result["answer"] == "컴퓨터과학과 추천 자격증 안내입니다."
    assert result["answer_type"] == "certification_list"
    assert [item["title"] for item in result["items"]] == ["정보처리기사", "SQLD"]
    assert all(item["source_url"] for item in result["items"])
    assert result["structured_intent"] == "career_certification"


def test_database_exam_scope_is_subject_specific(tmp_path) -> None:
    result = answer_question("데이터베이스 시험 범위는?", index=empty_index(tmp_path))
    other = answer_question("운영체제 시험 범위는?", index=empty_index(tmp_path))

    assert "현재 수집된 공식 데이터에서는 데이터베이스시스템 시험범위를 확인할 수 없습니다." in result["answer"]
    assert "임의로 안내하지 않습니다" in result["answer"]
    assert "13~15장" not in result["answer"]
    assert result["structured_intent"] == "exam_scope"
    assert result["actions"][0]["label"] == "학과 최근 공지 바로가기"
    assert other.get("structured_intent") == "exam_scope"
    assert "운영체제 시험범위를 확인할 수 없습니다" in other["answer"]


def test_exam_scope_uses_official_notice_evidence_when_available(tmp_path) -> None:
    index = SearchIndex(tmp_path / "exam-scope.json")
    index.rebuild(
        [
            {
                "title": "데이터베이스시스템 기말고사 시험범위 안내",
                "category": "공지사항",
                "document_type": "게시물",
                "body": "데이터베이스시스템 기말고사 시험범위는 공식 강의계획서 평가정보를 확인해 주세요.",
                "summary": "데이터베이스시스템 기말고사 시험범위 안내",
                "source_url": "https://cs.knou.ac.kr/bbs/cs1/2119/artclView.do",
                "source_type": "official",
                "collected_at": "2026-07-02T00:00:00+09:00",
                "published_at": "2026.07.02",
            }
        ]
    )

    result = answer_question("데이터베이스 시험범위는?", index=index)

    assert result["structured_intent"] == "exam_scope"
    assert result["answer"].startswith("공식 데이터에서 확인된 데이터베이스시스템 시험범위 안내입니다.")
    assert result["actions"][0]["label"] == "원문 보기"
    assert result["source_urls"][0].endswith("/artclView.do")
