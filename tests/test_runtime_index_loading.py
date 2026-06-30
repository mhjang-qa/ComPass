from pathlib import Path

import main
from search_index import SearchIndex


class FakeNotionClient:
    def ensure_knowledge_schema(self):
        return {"property_count": 14}

    def upsert_curated_knowledge(self):
        return {"신규": 0, "변경": 0, "유지": 3, "실패": 0}

    def upsert_many(self, documents):
        return {"신규": len(list(documents)), "변경": 0, "유지": 0, "실패": 0}

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
    monkeypatch.setattr(main, "REQUIRED_DOCUMENT_URLS", ())
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


def test_startup_uses_existing_index_file_without_notion(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "persisted-index.json"
    persisted = SearchIndex(index_path)
    persisted.rebuild(FakeNotionClient().knowledge_documents())

    class FailingNotionClient:
        def __init__(self):
            raise AssertionError("Notion should not be used when a persisted index exists")

    monkeypatch.setattr(main, "index", SearchIndex(index_path))
    monkeypatch.setattr(main, "NotionClient", FailingNotionClient)
    main.runtime_state.update(
        loading=False,
        notion_connected=False,
        notion_document_count=0,
        index_document_count=0,
        last_sync_at=None,
        last_error="",
    )

    main.initialize_search_index_on_startup()
    status = main.debug_index_payload()

    assert status["index_document_count"] == 1
    assert main.job_state["index"]["message"] == "검색 인덱스 로드 완료"
    assert main.job_state["notion"]["message"] == "검색 인덱스 파일을 메모리에 로드했습니다."


def test_startup_rebuilds_from_notion_when_index_file_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "index", SearchIndex(tmp_path / "missing-index.json"))
    monkeypatch.setattr(main, "NotionClient", FakeNotionClient)
    monkeypatch.setattr(main, "REQUIRED_DOCUMENT_URLS", ())
    monkeypatch.setattr(main.config, "NOTION_TOKEN", "test-token")
    main.runtime_state.update(
        loading=False,
        notion_connected=False,
        notion_document_count=0,
        index_document_count=0,
        last_sync_at=None,
        last_error="",
    )

    main.initialize_search_index_on_startup()
    status = main.debug_index_payload()

    assert status["index_document_count"] == 1
    assert status["notion_connected"] is True
    assert main.job_state["index"]["message"] == "검색 인덱스 자동 로딩 완료"
