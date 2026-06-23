from bs4 import BeautifulSoup
from pathlib import Path

from crawler import CommunityCrawler, KnouCrawler, clean_text


class AllowAllRobots:
    def can_fetch(self, user_agent: str, url: str) -> bool:
        return True


def crawler_without_network() -> KnouCrawler:
    crawler = object.__new__(KnouCrawler)
    crawler.allowed_path_prefixes = ("/cs1", "/sites/cs1", "/bbs/cs1")
    crawler.robots = AllowAllRobots()
    return crawler


def test_allows_real_department_paths() -> None:
    crawler = crawler_without_network()

    assert crawler.is_allowed("https://cs.knou.ac.kr/sites/cs1/index.do")
    assert crawler.is_allowed("https://cs.knou.ac.kr/cs1/4784/subview.do")
    assert crawler.is_allowed(
        "https://cs.knou.ac.kr/cs1/4812/subview.do?enc=board-detail"
    )
    assert crawler.is_allowed(
        "https://cs.knou.ac.kr/bbs/cs1/2119/802584/artclView.do"
    )
    assert not crawler.is_allowed("https://cs.knou.ac.kr/knou/561/subview.do")
    assert not crawler.is_allowed("https://regional.knou.ac.kr/regional/2479/subview.do")


def test_extracts_menu_links_before_page_cleanup() -> None:
    crawler = crawler_without_network()
    soup = BeautifulSoup(
        """
        <html>
          <header>
            <nav><a href="/cs1/4784/subview.do">학과장 인사말</a></nav>
          </header>
          <main><a href="/cs1/4812/subview.do?enc=notice">공지사항</a></main>
        </html>
        """,
        "lxml",
    )

    links = crawler._extract_links("https://cs.knou.ac.kr/sites/cs1/index.do", soup)

    assert links == [
        "https://cs.knou.ac.kr/cs1/4784/subview.do",
        "https://cs.knou.ac.kr/cs1/4812/subview.do?enc=notice",
    ]


def test_builds_javascript_board_pagination_links() -> None:
    crawler = crawler_without_network()
    soup = BeautifulSoup(
        """
        <form name="pageForm" action="/bbs/cs1/2119/artclList.do"></form>
        <p class="_pageState">
          <span class="_curPage">1</span>
          <span class="_totPage">3</span>
        </p>
        <a href="/bbs/cs1/2119/802584/artclView.do">게시물</a>
        <a href="javascript:page_link('2')">2</a>
        """,
        "lxml",
    )

    links = crawler._extract_links("https://cs.knou.ac.kr/cs1/4812/subview.do", soup)

    assert links == [
        "https://cs.knou.ac.kr/bbs/cs1/2119/802584/artclView.do",
        "https://cs.knou.ac.kr/bbs/cs1/2119/artclList.do?page=1",
        "https://cs.knou.ac.kr/bbs/cs1/2119/artclList.do?page=2",
        "https://cs.knou.ac.kr/bbs/cs1/2119/artclList.do?page=3",
    ]


class FakeResponse:
    headers = {"content-type": "text/html; charset=utf-8"}
    apparent_encoding = "utf-8"
    encoding = "utf-8"

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def get(self, url: str, timeout: int) -> FakeResponse:
        return FakeResponse(self.pages[url])


def test_crawl_respects_max_depth(tmp_path: Path, monkeypatch) -> None:
    start = "https://cs.knou.ac.kr/sites/cs1/index.do"
    child = "https://cs.knou.ac.kr/cs1/4784/subview.do"
    grandchild = "https://cs.knou.ac.kr/cs1/4803/subview.do"
    pages = {
        start: f"<html><title>메인</title><main>메인 내용<a href='{child}'>하위</a></main></html>",
        child: f"<html><title>하위</title><main>하위 내용<a href='{grandchild}'>손자</a></main></html>",
        grandchild: "<html><title>손자</title><main>손자 내용</main></html>",
    }

    crawler = object.__new__(KnouCrawler)
    crawler.start_url = start
    crawler.max_pages = 20
    crawler.max_depth = 1
    crawler.delay = 0
    crawler.allowed_path_prefixes = ("/cs1", "/sites/cs1", "/bbs/cs1")
    crawler.robots = AllowAllRobots()
    crawler.session = FakeSession(pages)
    monkeypatch.setattr("crawler.config.CRAWL_SNAPSHOT_PATH", tmp_path / "snapshot.json")
    monkeypatch.setattr("crawler.REQUIRED_DOCUMENT_URLS", ())
    monkeypatch.setattr("crawler.time.sleep", lambda _: None)

    progress = []
    documents = crawler.crawl(progress.append)

    assert [document.source_url for document in documents] == [start, child]
    assert all(item["depth"] <= 1 for item in progress)
    assert progress[-1]["documents"] == 2


def test_clean_text_removes_site_technical_noise() -> None:
    raw = """
    맞춤정보
    /WEB-INF/jsp/k2web/com/cop/site/layout.jsp
    fnctId=imageSlide,fnctNo=2133
    imageSlideSetupSeq=2133,cnvrsVe=1,stopTime=5
    교육목표
    교육목표
    컴퓨터과학과의 교육목표입니다.
    """

    assert clean_text(raw) == "교육목표\n컴퓨터과학과의 교육목표입니다."


def test_professor_page_is_always_seeded() -> None:
    from crawler import REQUIRED_DOCUMENT_URLS

    assert "https://cs.knou.ac.kr/cs1/4786/subview.do" in REQUIRED_DOCUMENT_URLS
    assert "https://cs.knou.ac.kr/cs1/4789/subview.do" in REQUIRED_DOCUMENT_URLS
    assert "https://cs.knou.ac.kr/cs1/4791/subview.do" in REQUIRED_DOCUMENT_URLS


def community_crawler_without_network() -> CommunityCrawler:
    crawler = object.__new__(CommunityCrawler)
    crawler.robots = AllowAllRobots()
    return crawler


def test_community_crawler_allows_only_public_board_routes() -> None:
    crawler = community_crawler_without_network()

    assert crawler.is_allowed("https://c-knou.com/computer_science")
    assert crawler.is_allowed("https://c-knou.com/computer_science/page/2")
    assert crawler.is_allowed("https://c-knou.com/computer_science/2883728", detail_only=True)
    assert not crawler.is_allowed("https://c-knou.com/computer_science/write")
    assert not crawler.is_allowed("https://c-knou.com/computer_science?search_keyword=시험")
    assert not crawler.is_allowed("https://c-knou.com/computer_science/comment/123/reply")
    assert not crawler.is_allowed("https://c-knou.com/other_board/123")


def test_community_parser_stores_excerpt_without_author_or_comments() -> None:
    crawler = community_crawler_without_network()
    soup = BeautifulSoup(
        """
        <div class="rd_hd">
          <strong class="catefl"><a>[질문]</a></strong>
          <span class="date">2026.06.22 09:22</span>
          <span class="np_16px"><a><strong>계절학기 난이도</strong></a></span>
          <a class="nick">작성자닉네임</a>
        </div>
        <div class="rd_body">
          <article>
            <div class="rhymix_content"><p>직장과 병행하며 계절학기를 준비하고 있습니다.</p></div>
          </article>
        </div>
        <div class="comment_1 rhymix_content">댓글 개인정보</div>
        """,
        "lxml",
    )

    document = crawler._parse_detail(
        "https://c-knou.com/computer_science/2883728",
        soup,
    )

    assert document is not None
    assert document.title == "계절학기 난이도"
    assert document.category == "비공식 커뮤니티 > 질문"
    assert document.published_at == "2026-06-22"
    assert document.source_type == "community"
    assert document.document_type == "커뮤니티게시물"
    assert "작성자닉네임" not in document.body
    assert "댓글 개인정보" not in document.body


def test_course_detail_specs_are_extracted_from_javascript_links() -> None:
    soup = BeautifulSoup(
        """
        <a href="javascript:jf_detailView('2026','1','3','34524','34');">
          <span>인공지능</span>
        </a>
        <a href="javascript:jf_detailView('2026','1','1','34174','34');">
          <span>파이썬프로그래밍기초</span>
        </a>
        """,
        "lxml",
    )

    specs = KnouCrawler._course_detail_specs(soup)

    assert specs == [
        {
            "course_name": "인공지능",
            "year": "2026",
            "semester": "1",
            "grade": "3",
            "course_code": "34524",
            "department_code": "34",
        },
        {
            "course_name": "파이썬프로그래밍기초",
            "year": "2026",
            "semester": "1",
            "grade": "1",
            "course_code": "34174",
            "department_code": "34",
        },
    ]


def test_course_detail_page_is_normalized_as_course_document() -> None:
    crawler = crawler_without_network()
    soup = BeautifulSoup(
        """
        <div id="outlineArea">
          <h5>개요</h5>
          운영체제의 구조와 프로세스 및 메모리 관리 원리를 학습한다.
          <h5>매체명</h5>
          멀티미디어 강의
          <h5>강의내용</h5>
          <table>
            <tbody>
              <tr><td>1</td><td>프로세스 관리</td><td>프로세스와 스레드</td></tr>
              <tr><td>2</td><td>메모리 관리</td><td>가상 메모리</td></tr>
            </tbody>
          </table>
        </div>
        """,
        "lxml",
    )
    spec = {
        "course_name": "운영체제",
        "year": "2026",
        "semester": "1",
        "grade": "3",
        "course_code": "34416",
        "department_code": "34",
    }

    document = crawler._parse_course_detail(spec, soup).finalize()

    assert document.document_type == "과목상세"
    assert document.source_url.endswith("#course-34416")
    assert document.normalized_items[0]["overview"].startswith("운영체제의 구조")
    assert document.normalized_items[0]["topics"] == ["프로세스 관리", "메모리 관리"]


def test_curriculum_table_is_normalized() -> None:
    soup = BeautifulSoup(
        """
        <table>
          <tr>
            <th rowspan="2">학년 학기</th><th rowspan="2">교과 구분</th>
            <th rowspan="2">교과목명</th><th rowspan="2">교과목 코드</th>
            <th colspan="2">강의매체</th><th colspan="2">평가방법</th>
          </tr>
          <tr><th>TV</th><th>웹강의</th><th>중간평가</th><th>기말평가</th></tr>
          <tr><td>1-1 (2026)</td><td>전공</td><td>컴퓨터의이해</td><td>34172</td><td>O</td><td>O</td><td>O</td><td>O</td></tr>
        </table>
        """,
        "lxml",
    )

    headers, rows, items = KnouCrawler._extract_tables(soup)

    assert "강의매체 / TV" in headers
    assert rows[0][2] == "컴퓨터의이해"
    assert items == [
        {
            "course_name": "컴퓨터의이해",
            "grade": "1학년",
            "semester": "1학기",
            "category": "전공",
            "course_code": "34172",
            "credit": "",
            "media": ["TV", "웹강의"],
            "evaluation": ["중간평가", "기말평가"],
        }
    ]
