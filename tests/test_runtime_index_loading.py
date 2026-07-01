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
    assert main.job_state["index"]["message"] == "검색 가능"
    assert main.job_state["notion"]["message"] == "검색 인덱스 파일을 메모리에 로드했습니다."


def test_startup_bootstraps_from_bundled_documents_when_index_file_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "index", SearchIndex(tmp_path / "missing-index.json"))
    monkeypatch.setattr(
        main,
        "bundled_bootstrap_documents",
        lambda: FakeNotionClient().knowledge_documents(),
    )
    monkeypatch.setattr(main.config, "NOTION_TOKEN", "")
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
    assert main.job_state["index"]["message"] == "검색 가능"
    assert main.job_state["notion"]["message"] == "번들된 공식 데이터로 검색 인덱스를 즉시 구성했습니다."


def test_startup_rebuilds_from_notion_when_no_index_or_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "index", SearchIndex(tmp_path / "missing-index.json"))
    monkeypatch.setattr(main, "NotionClient", FakeNotionClient)
    monkeypatch.setattr(main, "REQUIRED_DOCUMENT_URLS", ())
    monkeypatch.setattr(main, "bundled_bootstrap_documents", lambda: [])
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


def test_chat_returns_retryable_loading_while_index_lock_is_busy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(main, "index", SearchIndex(tmp_path / "missing-index.json"))
    main.runtime_state.update(
        loading=True,
        index_loading=True,
        index_ready=False,
        index_state="loading",
        notion_connected=False,
        notion_document_count=0,
        index_document_count=0,
        last_sync_at=None,
        last_error="",
    )

    assert main.index_load_lock.acquire(blocking=False) is True
    try:
        response = main.chat(
            main.ChatRequest(
                question="공식 문서 검색 테스트",
                session_id="test-session",
                request_id="test-request",
            )
        )
    finally:
        main.index_load_lock.release()
        main.runtime_state.update(loading=False, index_loading=False, index_ready=False, index_state="stale")

    assert response["status"] == "index_loading"
    assert response["mode"] == "INDEX_LOADING"
    assert response["retry_after_ms"] == 1500


def test_public_index_status_exposes_ready_without_admin_details(tmp_path: Path, monkeypatch) -> None:
    search_index = SearchIndex(tmp_path / "index.json")
    search_index.rebuild(FakeNotionClient().knowledge_documents())
    monkeypatch.setattr(main, "index", search_index)
    main.runtime_state.update(
        loading=False,
        index_loading=False,
        index_ready=True,
        index_state="ready",
        index_document_count=1,
        index_last_error="",
        retry_after_ms=1500,
    )

    response = main.index_status(None)

    assert response["ready"] is True
    assert response["indexed"] == 1
    assert response["state"] == "ready"
    assert "runtime" not in response
    assert "job" not in response
