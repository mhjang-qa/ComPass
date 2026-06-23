from pathlib import Path

from chatbot import retrieve_documents
from search_index import SearchIndex


def test_chatbot_retrieval_excludes_unofficial_community_documents(tmp_path: Path) -> None:
    index = SearchIndex(tmp_path / "index.json")
    index.rebuild(
        [
            {
                "title": "공식 교육과정",
                "category": "교육과정",
                "document_type": "교육과정표",
                "summary": "컴퓨터과학과 공식 교육과정 안내",
                "search_text": "컴퓨터과학과 교육과정 과목 안내",
                "source_url": "https://cs.knou.ac.kr/cs1/4789/subview.do",
                "source_type": "official",
            },
            {
                "title": "학생이 정리한 교육과정 후기",
                "category": "비공식 커뮤니티 > 정보",
                "document_type": "커뮤니티게시물",
                "summary": "교육과정 수강 후기",
                "search_text": "컴퓨터과학과 교육과정 과목 안내 후기",
                "source_url": "https://c-knou.com/computer_science/123",
                "source_type": "community",
            },
        ]
    )

    hits = retrieve_documents(index, "컴퓨터과학과 교육과정을 알려줘", "curriculum")

    assert hits
    assert all(hit["source_type"] == "official" for hit in hits)
