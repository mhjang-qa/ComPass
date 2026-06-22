from pathlib import Path

import main
from search_index import SearchIndex


class FakeNotionClient:
    def ensure_knowledge_schema(self):
        return {"property_count": 14}

    def upsert_curated_knowledge(self):
        return {"신규": 0, "변경": 0, "유지": 3, "실패": 0}

    def knowledge_documents(self):
        return [
            {
                "title": "교수진 소개",
                "category": "교수진",
                "document_type": "일반페이지",
                "body": "손진곤 교수 이메일 jgshon@knou.ac.kr",
                "summary": "교수진 공식 정보",
                "source_url": "https://cs.knou.ac.kr/cs1/4786/subview.do",
                "keywords": ["교수진"],
                "search_text": "교수진 소개 손진곤 교수",
            }
        ]


def test_lazy_index_loading_from_notion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "index", SearchIndex(tmp_path / "index.json"))
    monkeypatch.setattr(main, "NotionClient", FakeNotionClient)
    monkeypatch.setattr(main.config, "NOTION_TOKEN", "test-token")
    main.runtime_state.update(
        loading=False,
        notion_connected=False,
        notion_document_count=0,
        index_document_count=0,
        last_sync_at=None,
        last_error="",
    )

    loaded = main.ensure_search_index(force=True, reason="test")
    status = main.debug_index_payload()

    assert loaded is True
    assert status["notion_connected"] is True
    assert status["notion_document_count"] == 1
    assert status["index_document_count"] == 1
    assert status["last_sync_at"]
    assert status["knowledge_db_id_masked"].startswith("38773f")

