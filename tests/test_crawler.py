from bs4 import BeautifulSoup

from crawler import KnouCrawler


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
