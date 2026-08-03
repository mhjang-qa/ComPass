from pathlib import Path

from chatbot import answer_question
from crawler import extract_schedule_items
from stats import display_answer_text


class FakeIndex:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, top_k=5, filters=None):
        return self.hits[:top_k]


def test_notice_answer_is_summary_first_without_raw_metadata() -> None:
    hits = [
        {
            "title": "2026 총장배 소프트웨어경진대회",
            "category": "공지사항",
            "published_at": "2026-05-20",
            "body": (
                "글번호: 799004\n카테고리: 공지사항\n게시일: 2026-05-20\n"
                "참가 신청과 작품 제출 일정을 안내합니다. 자세한 내용은 공식 공지를 확인해 주세요.\n"
                "첨부파일: 참가신청서.hwp"
            ),
            "source_url": "https://cs.knou.ac.kr/bbs/cs1/2119/799004/artclView.do",
            "score": 95,
        }
    ]

    result = answer_question("컴퓨터과학과 최근 공지를 알려줘", index=FakeIndex(hits))

    assert result["answer_type"] == "notice_list"
    assert result["display_limit"] == 3
    assert result["items"][0]["title"] == "2026 총장배 소프트웨어경진대회"
    assert len(result["items"][0]["description"]) <= 80
    assert "글번호" not in result["items"][0]["description"]
    assert "첨부파일" not in result["items"][0]["description"]
    assert result["actions"][-1]["label"] == "공지 더보기"
    assert result["items"][0]["source_url"].endswith("799004/artclView.do")
    assert result["items"][0]["link_label"] == "공지 바로가기"


def test_competition_question_routes_to_notice_answer() -> None:
    hits = [
        {
            "title": "2026 총장배 소프트웨어경진대회",
            "category": "공지사항",
            "published_at": "2026-05-20",
            "body": "참가 신청과 작품 제출 일정을 안내합니다. 자세한 내용은 공식 공지를 확인해 주세요.",
            "source_url": "https://cs.knou.ac.kr/bbs/cs1/2119/799004/artclView.do",
            "score": 95,
        }
    ]

    result = answer_question("소프트웨어 경진대회", index=FakeIndex(hits))

    assert result["answer_type"] == "notice_list"
    assert result["items"][0]["title"] == "2026 총장배 소프트웨어경진대회"
    assert result["sources"][0]["url"].endswith("799004/artclView.do")
    assert result["answer_type"] != "out_of_scope"


def test_schedule_parser_and_answer_hide_calendar_raw_text() -> None:
    raw = """
    월간 일정 2026년
    SUN | MON | TUE | WED | THU | FRI | SAT
    07.23 ~ 07.29 | 2026. 1차 졸업논문계획서 신청
    09.01 | 2026. 2학기 시작
    """
    parsed = extract_schedule_items(raw)
    assert parsed[0]["title"] == "1차 졸업논문계획서 신청"
    assert parsed[0]["start_date"] == "2026-07-23"
    assert parsed[0]["end_date"] == "2026-07-29"

    hits = [
        {
            "title": "학과일정",
            "category": "학과일정",
            "body": raw,
            "normalized_items": parsed,
            "source_url": "https://cs.knou.ac.kr/cs1/4812/subview.do",
            "score": 99,
        }
    ]
    result = answer_question("컴퓨터과학과 학과 일정을 알려줘", index=FakeIndex(hits))

    assert result["answer_type"] == "schedule_list"
    assert result["items"][0]["title"] == "2학기 시작"
    assert all("SUN" not in str(item) for item in result["items"])
    assert result["actions"][-1]["label"] == "학과 일정 바로가기"
    assert result["actions"][-1]["url"] == "https://cs.knou.ac.kr/cs1/4812/subview.do"


def test_schedule_answer_extracts_loose_saved_schedule_lines() -> None:
    raw = """
    학과일정 2026년
    08.04 - 08.08 2학기 수강신청
    09.01 2026학년도 2학기 개강
    """

    hits = [
        {
            "title": "학과일정",
            "category": "학과일정",
            "body": raw,
            "source_url": "https://cs.knou.ac.kr/cs1/4812/subview.do",
            "score": 99,
        }
    ]
    result = answer_question("컴퓨터과학과 학과 일정을 알려줘", index=FakeIndex(hits))

    assert result["answer_type"] == "schedule_list"
    assert result["items"]
    assert result["items"][0]["title"] == "2026학년도 2학기 개강"
    assert result["items"][1]["title"] == "2학기 수강신청"
    assert "저장된 최신 일정 데이터가 부족" not in result["summary"]


def test_stats_answer_text_includes_structured_items() -> None:
    text = display_answer_text(
        {
            "answer": "학과 일정 안내입니다.",
            "summary": "컴퓨터과학과 주요 학과 일정 3건을 안내드립니다.",
            "items": [
                {
                    "title": "2학기 수강신청",
                    "start_date": "2026-08-04",
                    "end_date": "2026-08-08",
                    "description": "수강신청 기간입니다.",
                }
            ],
        }
    )

    assert "학과 일정 안내입니다." in text
    assert "2학기 수강신청" in text
    assert "2026-08-04" in text


def test_schedule_answer_rejects_non_official_schedule_sources() -> None:
    hits = [
        {
            "title": "547",
            "category": "학생광장",
            "body": "벼룩시장 중고장터 일정",
            "normalized_items": [
                {
                    "title": "547",
                    "start_date": "2026-06-23",
                    "description": "벼룩시장 중고장터 일정",
                    "source_url": "https://cs.knou.ac.kr/cs1/4792/subview.do",
                }
            ],
            "source_url": "https://cs.knou.ac.kr/cs1/4792/subview.do",
            "score": 99,
        }
    ]

    result = answer_question("학과 일정 알려줘", index=FakeIndex(hits))

    assert result["answer_type"] == "schedule_list"
    assert len(result["items"]) == 3
    assert "공식 일정 확인 항목" in result["summary"]
    assert result["actions"][-1]["url"] == "https://cs.knou.ac.kr/cs1/4812/subview.do"


def test_loading_animation_and_compact_message_layout_are_present() -> None:
    script = Path("static/app.js").read_text(encoding="utf-8")
    style = Path("static/style.css").read_text(encoding="utf-8")

    assert "function createSearchLoading()" in script
    assert "}, 40);" in script
    assert "}, 800);" in script
    assert "잠시만 기다려주세요." in script
    assert "new ResizeObserver" in script
    assert ".messages > .message:first-child {margin-top: 0;}" in style
    assert "var(--composer-height)\\n    + var(--quick-menu-height)" not in style
