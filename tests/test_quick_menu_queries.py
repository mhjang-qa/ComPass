from pathlib import Path

from chatbot import answer_question
from search_index import CURRICULUM_URL, FACULTY_URL, SCHEDULE_URL, SearchIndex


def quick_index(tmp_path: Path) -> SearchIndex:
    index = SearchIndex(tmp_path / "quick-index.json")
    index.rebuild(
        [
            {
                "title": "교수진 소개",
                "category": "교수진",
                "body": "손진곤\n교수 이메일 jgshon@knou.ac.kr 연락처 02-3668-4656",
                "summary": "교수진 정보",
                "source_url": FACULTY_URL,
                "keywords": ["교수진"],
                "search_text": "컴퓨터과학과 교수진 소개 손진곤 교수",
            },
            {
                "title": "교과과정",
                "category": "교과정보 > 교육과정",
                "document_type": "일반페이지",
                "body": "컴퓨터과학과 교과과정과 학년별 전공과목을 안내합니다.",
                "summary": "교육과정 안내",
                "source_url": CURRICULUM_URL,
                "keywords": ["교육과정"],
                "search_text": "컴퓨터과학과 교육과정 교과과정",
                "normalized_items": [
                    {
                        "course_name": "컴퓨터의이해",
                        "grade": "1학년",
                        "semester": "1학기",
                        "category": "전공",
                        "course_code": "34172",
                        "credit": "3",
                        "media": ["TV", "웹강의"],
                        "evaluation": ["중간평가", "기말평가"],
                    }
                ],
            },
            {
                "title": "공지사항",
                "category": "학과광장 > 공지사항",
                "document_type": "게시물",
                "body": "최근 컴퓨터과학과 공지사항입니다. 소프트웨어경진대회 참가 안내입니다.",
                "summary": "최근 공지",
                "source_url": "https://cs.knou.ac.kr/cs1/4812/subview.do",
                "published_at": "2026-06-20",
                "keywords": ["최근", "공지"],
                "search_text": "컴퓨터과학과 최근 공지 공지사항",
            },
            {
                "title": "학과일정",
                "category": "학습정보 > 학과일정",
                "document_type": "일반페이지",
                "body": "컴퓨터과학과의 월별 학과 일정을 안내합니다.",
                "summary": "학과 일정",
                "source_url": SCHEDULE_URL,
                "keywords": ["학과일정"],
                "search_text": "컴퓨터과학과 학과 일정 학사일정",
            },
            {
                "title": "교수님 특별 강연",
                "category": "교수진",
                "document_type": "게시물",
                "body": "교수님 특별 강연 안내",
                "summary": "교수 관련 게시글",
                "source_url": "https://cs.knou.ac.kr/bbs/cs1/2119/1/artclView.do",
                "keywords": ["교수진"],
                "search_text": "교수진 교수님 특별 강연",
            },
            {
                "title": "외부 교육과정 모집",
                "category": "교육과정",
                "document_type": "게시물",
                "body": "외부 취업 교육과정 모집 안내",
                "summary": "외부 교육",
                "source_url": "https://cs.knou.ac.kr/bbs/cs1/2284/2/artclView.do",
                "keywords": ["교육과정"],
                "search_text": "교육과정 교육과정 교육과정 외부 취업 교육",
            },
            {
                "title": "행사 일정 안내",
                "category": "학과일정",
                "document_type": "게시물",
                "body": "학과 행사 일정",
                "summary": "행사 일정",
                "source_url": "https://cs.knou.ac.kr/bbs/cs1/2119/3/artclView.do",
                "keywords": ["학과일정"],
                "search_text": "학과일정 학과일정 학과 행사 일정",
            },
        ]
    )
    return index


def test_all_quick_menu_queries_find_db_documents(tmp_path: Path) -> None:
    index = quick_index(tmp_path)
    questions = [
        "컴퓨터과학과 교수진 정보를 알려줘",
        "컴퓨터과학과 교육과정을 알려줘",
        "컴퓨터과학과 최근 공지를 알려줘",
        "컴퓨터과학과 학과 일정을 알려줘",
    ]

    results = [answer_question(question, index=index) for question in questions]

    assert all(result["mode"] == "DB검색" for result in results)
    assert all(result["sources"] for result in results)
    assert [result["sources"][0]["url"] for result in results] == [
        FACULTY_URL,
        CURRICULUM_URL,
        "https://cs.knou.ac.kr/cs1/4812/subview.do",
        SCHEDULE_URL,
    ]
    assert results[1]["answer_type"] == "course_table"
    assert results[1]["items"][0]["course_name"] == "컴퓨터의이해"
    assert "학년 | 학기" not in results[1]["answer"]
    assert results[2]["answer_type"] == "notice_list"
    assert results[3]["answer_type"] == "schedule_list"


def test_frontend_uses_required_quick_queries() -> None:
    html = Path("templates/index.html").read_text(encoding="utf-8")

    for query in (
        "컴퓨터과학과 교수진 정보를 알려줘",
        "컴퓨터과학과 교육과정을 알려줘",
        "컴퓨터과학과 최근 공지를 알려줘",
        "컴퓨터과학과 학과 일정을 알려줘",
    ):
        assert f'data-question="{query}"' in html
