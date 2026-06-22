from crawler import CrawlDocument
from notion_client import NotionClient


def document(body: str = "동일한 본문") -> CrawlDocument:
    return CrawlDocument(
        title="테스트 문서",
        category="테스트",
        body=body,
        source_url="https://cs.knou.ac.kr/cs1/test/subview.do",
        collected_at="2026-06-22T12:00:00+09:00",
    ).finalize()


class FakeNotionClient(NotionClient):
    def __init__(self, existing_hash: str | None) -> None:
        self.existing_hash = existing_hash
        self.calls = []
        self.blocks_replaced = False

    def find_by_url(self, database_id: str, url: str):
        if self.existing_hash is None:
            return None
        return {
            "id": "page-id",
            "properties": {
                "콘텐츠해시": {
                    "type": "rich_text",
                    "rich_text": [{"plain_text": self.existing_hash}],
                }
            },
        }

    def request(self, method: str, path: str, payload=None):
        self.calls.append((method, path, payload))
        return {}

    def replace_page_blocks(self, page_id: str, doc: CrawlDocument) -> None:
        self.blocks_replaced = True


def test_unchanged_document_does_not_write_to_notion() -> None:
    doc = document()
    client = FakeNotionClient(doc.content_hash)

    status = client.upsert_document(doc)

    assert status == "유지"
    assert client.calls == []
    assert client.blocks_replaced is False


def test_changed_document_updates_properties_and_blocks() -> None:
    doc = document("변경된 본문")
    client = FakeNotionClient("old-hash")

    status = client.upsert_document(doc)

    assert status == "변경"
    assert len(client.calls) == 1
    assert client.calls[0][0:2] == ("PATCH", "/pages/page-id")
    assert client.blocks_replaced is True


def test_new_document_is_created() -> None:
    doc = document()
    client = FakeNotionClient(None)

    status = client.upsert_document(doc)

    assert status == "신규"
    assert len(client.calls) == 1
    assert client.calls[0][0:2] == ("POST", "/pages")
