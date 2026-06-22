from chatbot import answer_question
from search_index import FACULTY_URL, SearchIndex


FACULTY_BODY = """손진곤
교수 이메일 jgshon@knou.ac.kr 연락처 02-3668-4656 담당과목(대학) 이산수학
이병래
교수 이메일 brlee@knou.ac.kr 연락처 02-3668-4653 담당과목(대학) 인공지능
"""


def test_faculty_intent_only_uses_faculty_document(tmp_path) -> None:
    index = SearchIndex(tmp_path / "index.json")
    index.rebuild(
        [
            {
                "title": "교수진 소개",
                "category": "교수진",
                "body": FACULTY_BODY,
                "summary": "교수진 공식 정보",
                "source_url": FACULTY_URL,
                "keywords": ["교수진"],
                "search_text": f"교수진 소개 {FACULTY_BODY}",
            },
            {
                "title": "학과장 인사말",
                "category": "학과소개",
                "body": "전 산업 분야에서 인공지능과 빅데이터가 중요합니다.",
                "summary": "학과장 인사말",
                "source_url": "https://cs.knou.ac.kr/cs1/4784/subview.do",
                "keywords": ["인공지능"],
                "search_text": "교수 학과장 인공지능 빅데이터",
            },
        ]
    )

    result = answer_question("교수진 정보를 알려줘", index=index)

    assert result["mode"] == "DB검색"
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == FACULTY_URL
    assert result["answer"] == "컴퓨터과학과 교수진 정보입니다."
    assert [item["name"] for item in result["items"]] == ["손진곤", "이병래"]
    assert "전 산업 분야" not in result["answer"]
    assert result["answer_type"] == "faculty"
    assert result["total_count"] == 2
    assert result["items"][0] == {
        "name": "손진곤",
        "title": "교수",
        "email": "jgshon@knou.ac.kr",
        "phone": "02-3668-4656",
        "subjects_undergraduate": ["이산수학"],
        "subjects_graduate": [],
        "source_url": FACULTY_URL,
    }
