"""한국방송통신대학교 컴퓨터과학과 공개 페이지 크롤러."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid"}
EXCLUDED_HINTS = ("login", "logout", "sso", "signin", "javascript:", "mailto:", "tel:")
TECHNICAL_LINE_PATTERNS = (
    re.compile(r"^/?WEB.?INF/", re.IGNORECASE),
    re.compile(r"^(?:fnctId|fnctNo|imageSlideSetupSeq|recentBbsSetupSeq)=", re.IGNORECASE),
    re.compile(r"^(?:cs1_)?JW_[A-Z0-9_]+$", re.IGNORECASE),
    re.compile(r"^(?:cnvrsVe|stopTime|pcCo|cnvrsMth|pcMgWidth|isImageNoHandlr)", re.IGNORECASE),
    re.compile(r"^(?:글번호|조회수)\s*[:：]?\s*\d+\s*$"),
    re.compile(r"^(?:작성자|카테고리|게시일)\s*[:：]\s*$"),
)
BOILERPLATE_LINES = {
    "맞춤정보", "확대", "기본", "축소", "통합검색", "사이트맵", "모바일 메뉴 열기",
    "모바일 메뉴 닫기", "Search", "검색", "닫기", "PREV", "NEXT", "슬라이드 재생",
    "슬라이드 정지", "슬라이드 멈춤", "이전 슬라이드", "다음 슬라이드",
    "오늘 하루 열지않기", "COPYRIGHTⓒKOREA NATIONAL OPEN UNIVERSITY. ALL RIGHTS RESERVED.",
}
DATE_PATTERNS = (
    re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
)
ATTACHMENT_EXTENSIONS = (
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".txt", ".csv", ".jpg", ".jpeg", ".png",
)
REQUIRED_DOCUMENT_URLS = (
    "https://cs.knou.ac.kr/cs1/4786/subview.do",
    "https://cs.knou.ac.kr/cs1/4789/subview.do",
    "https://cs.knou.ac.kr/cs1/4791/subview.do",
)
COURSE_GUIDE_URL = "https://cs.knou.ac.kr/cs1/4791/subview.do"
COURSE_DETAIL_ENDPOINT = "https://cs.knou.ac.kr/learningInformation/cs1/view.do"


@dataclass
class CrawlDocument:
    title: str
    category: str
    body: str
    source_url: str
    collected_at: str
    published_at: str = ""
    keywords: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    summary: str = ""
    content_hash: str = ""
    status: str = "신규"
    search_text: str = ""
    document_type: str = "일반페이지"
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    normalized_items: list[dict[str, Any]] = field(default_factory=list)
    source_type: str = "official"
    source_label: str = "한국방송통신대학교 컴퓨터과학과 공식 홈페이지"

    def finalize(self) -> "CrawlDocument":
        self.body = clean_text(self.body)
        self.summary = summarize(self.body)
        self.keywords = extract_keywords(f"{self.title} {self.category} {self.body}")
        normalized_text = " ".join(
            " ".join(str(value) for value in item.values() if value)
            for item in self.normalized_items
        )
        searchable_body = normalized_text or self.summary or self.body[:1000]
        self.search_text = clean_text(
            " ".join([self.title, self.category, self.summary, " ".join(self.keywords), searchable_body])
        )
        digest_source = json.dumps(
            {
                "title": self.title,
                "category": self.category,
                "body": self.body,
                "published_at": self.published_at,
                "attachments": sorted(self.attachments),
                "table_headers": self.table_headers,
                "table_rows": self.table_rows,
                "normalized_items": self.normalized_items,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.content_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        if self.source_type == "community":
            self.document_type = "커뮤니티게시물"
        else:
            self.document_type = classify_document_type(self.source_url, self.category)
        if self.source_type != "community" and self.normalized_items and any(item.get("overview") for item in self.normalized_items):
            self.document_type = "과목상세"
            self.category = "교과정보 > 교과목안내 > 과목상세"
        elif self.source_url.startswith(COURSE_GUIDE_URL):
            self.document_type = "교과목목록"
            self.category = "교과정보 > 교과목안내"
        elif self.normalized_items and any(item.get("course_name") for item in self.normalized_items):
            self.document_type = "교육과정표"
        elif self.normalized_items and any(item.get("start_date") for item in self.normalized_items):
            self.document_type = "학과일정"
            self.category = "학과일정"
        return self


def clean_text(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        if not line or line in BOILERPLATE_LINES:
            continue
        if any(pattern.search(line) for pattern in TECHNICAL_LINE_PATTERNS):
            continue
        if cleaned and cleaned[-1] == line:
            continue
        line = re.sub(r"^[★☆■□◆◇▶▷●○]+\s*", "", line).strip()
        if not line:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def summarize(text: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    cut = compact[:limit]
    sentence_end = max(cut.rfind("."), cut.rfind("다."), cut.rfind("요."))
    return (cut[: sentence_end + 1] if sentence_end > limit // 2 else cut).strip() + "…"


def extract_keywords(text: str, limit: int = 15) -> list[str]:
    stopwords = {
        "그리고", "그러나", "대한", "관련", "안내", "입니다", "합니다", "있는", "없는",
        "에서", "으로", "에게", "까지", "부터", "컴퓨터과학과", "한국방송통신대학교",
    }
    tokens = re.findall(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9+.#_-]{1,}", (text or "").lower())
    counts: dict[str, int] = {}
    for token in tokens:
        if token in stopwords or token.isdigit() or len(token) < 2:
            continue
        counts[token] = counts.get(token, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def extract_schedule_items(text: str) -> list[dict[str, str]]:
    """월간 달력 원문에서 실제 일정 행만 추출한다."""
    if not text:
        return []

    year_match = re.search(r"(20\d{2})\s*년", text)
    default_year = int(year_match.group(1)) if year_match else datetime.now().year
    pattern = re.compile(
        r"(?P<start>\d{1,2}\.\d{1,2})"
        r"(?:\s*~\s*(?P<end>\d{1,2}\.\d{1,2}))?"
        r"\s*\|\s*(?P<title>[^\n|]+)"
    )
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in pattern.finditer(text):
        raw_title = clean_text(match.group("title")).strip("-· ")
        if not raw_title:
            continue
        title_year = re.search(r"(20\d{2})", raw_title)
        year = int(title_year.group(1)) if title_year else default_year
        title = re.sub(r"^20\d{2}[.\s년]*", "", raw_title).strip() or raw_title

        def to_iso(value: str, target_year: int) -> str:
            month, day = (int(part) for part in value.split(".", 1))
            return datetime(target_year, month, day).date().isoformat()

        try:
            start_date = to_iso(match.group("start"), year)
            end_raw = match.group("end")
            end_year = year
            if end_raw:
                start_month = int(match.group("start").split(".", 1)[0])
                end_month = int(end_raw.split(".", 1)[0])
                if end_month < start_month:
                    end_year += 1
                end_date = to_iso(end_raw, end_year)
            else:
                end_date = start_date
        except ValueError:
            continue

        key = (title, start_date, end_date)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "start_date": start_date,
                "end_date": end_date,
                "description": f"{title} 관련 학과 일정",
                "category": "학과일정",
            }
        )
    return items


def classify_document_type(url: str, category: str) -> str:
    path = urlsplit(url).path.lower()
    if "artclview.do" in path:
        return "게시물"
    if "artcllist.do" in path or "게시판" in category or "공지" in category:
        return "게시판목록"
    if path.endswith("/index.do"):
        return "메인"
    return "일반페이지"


def normalize_url(url: str, base_url: str = "") -> str:
    absolute = urljoin(base_url, (url or "").strip())
    parts = urlsplit(absolute)
    if parts.scheme not in {"http", "https"}:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


class KnouCrawler:
    def __init__(
        self,
        start_url: str = config.CRAWL_START_URL,
        max_pages: int = config.CRAWL_MAX_PAGES,
        delay: float = config.CRAWL_DELAY_SECONDS,
        max_depth: int = 3,
    ) -> None:
        self.start_url = normalize_url(start_url)
        self.max_pages = max_pages
        self.delay = max(delay, 0.2)
        self.max_depth = max(0, max_depth)
        configured_prefixes = {
            prefix.strip()
            for prefix in config.ALLOWED_PATH_PREFIX.split(",")
            if prefix.strip()
        }
        # 기존 Render 환경변수가 /sites/cs1 하나로 남아 있어도 실제 메뉴·게시판 경로를 누락하지 않는다.
        self.allowed_path_prefixes = tuple(
            sorted(
                configured_prefixes
                | {"/cs1", "/sites/cs1", "/bbs/cs1", "/learningInformation/cs1"}
            )
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
        self.robots = self._load_robots()

    def _load_robots(self) -> RobotFileParser:
        parser = RobotFileParser()
        robots_url = f"https://{config.ALLOWED_DOMAIN}/robots.txt"
        parser.set_url(robots_url)
        try:
            response = self.session.get(robots_url, timeout=config.CRAWL_TIMEOUT_SECONDS)
            if response.ok:
                parser.parse(response.text.splitlines())
            else:
                parser.parse([])
        except requests.RequestException as exc:
            logger.warning("robots.txt 확인 실패, 보수적 delay를 유지합니다: %s", exc)
            parser.parse([])
        return parser

    @staticmethod
    def _is_login_or_external_hint(url: str) -> bool:
        lowered = (url or "").lower()
        return any(hint in lowered for hint in EXCLUDED_HINTS)

    def is_allowed(self, url: str) -> bool:
        if not url or self._is_login_or_external_hint(url):
            return False
        parts = urlsplit(url)
        if parts.netloc != config.ALLOWED_DOMAIN:
            return False
        if not any(parts.path.startswith(prefix) for prefix in self.allowed_path_prefixes):
            return False
        return self.robots.can_fetch(config.USER_AGENT, url)

    def crawl(self, progress: Callable[[dict[str, Any]], None] | None = None) -> list[CrawlDocument]:
        seed_urls = [self.start_url, *REQUIRED_DOCUMENT_URLS]
        queue = deque((url, 0) for url in dict.fromkeys(seed_urls))
        enqueued: set[str] = set(seed_urls)
        seen: set[str] = set()
        documents: list[CrawlDocument] = []

        while queue and len(seen) < self.max_pages:
            url, depth = queue.popleft()
            if url in seen or not self.is_allowed(url):
                continue
            seen.add(url)
            if progress:
                progress(
                    {
                        "visited": len(seen),
                        "queued": len(queue),
                        "documents": len(documents),
                        "depth": depth,
                        "max_depth": self.max_depth,
                        "url": url,
                        "percent": min(94, max(2, round(len(seen) / self.max_pages * 100))),
                    }
                )
            try:
                response = self.session.get(url, timeout=config.CRAWL_TIMEOUT_SECONDS)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    continue
                response.encoding = response.apparent_encoding or response.encoding
                soup = BeautifulSoup(response.text, "lxml")
                # 본문 정제 과정에서 header/nav가 제거되기 전에 메뉴와 게시물 링크를 먼저 확보한다.
                discovered_links = self._extract_links(url, soup)
                document = self._parse_page(url, soup)
                if document and document.body:
                    documents.append(document.finalize())
                if url == COURSE_GUIDE_URL:
                    detail_documents = self._fetch_course_detail_documents(soup, progress)
                    documents.extend(detail_documents)
                if depth < self.max_depth:
                    for link in discovered_links:
                        if link not in seen and link not in enqueued:
                            queue.append((link, depth + 1))
                            enqueued.add(link)
                if progress:
                    discovered_total = len(seen) + len(queue)
                    progress(
                        {
                            "visited": len(seen),
                            "queued": len(queue),
                            "documents": len(documents),
                            "depth": depth,
                            "max_depth": self.max_depth,
                            "url": url,
                            "percent": min(
                                94,
                                max(2, round(len(seen) / max(discovered_total, 1) * 100)),
                            ),
                        }
                    )
            except requests.RequestException as exc:
                logger.warning("페이지 수집 실패 url=%s error=%s", url, exc)
            except Exception:
                logger.exception("페이지 파싱 실패 url=%s", url)
            time.sleep(self.delay)

        self.save_snapshot(documents, config.CRAWL_SNAPSHOT_PATH)
        logger.info(
            "크롤링 완료: 최대깊이=%d 방문=%d 문서=%d",
            self.max_depth,
            len(seen),
            len(documents),
        )
        return documents

    def fetch_document(self, url: str) -> CrawlDocument | None:
        """필수 공식 페이지 한 건을 수집한다. 앱 초기 지식 보장에 사용한다."""
        normalized = normalize_url(url)
        if not self.is_allowed(normalized):
            logger.warning("필수 페이지 수집 제외 url=%s", normalized)
            return None
        try:
            response = self.session.get(normalized, timeout=config.CRAWL_TIMEOUT_SECONDS)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", ""):
                logger.warning("필수 페이지가 HTML이 아닙니다 url=%s", normalized)
                return None
            response.encoding = response.apparent_encoding or response.encoding
            document = self._parse_page(normalized, BeautifulSoup(response.text, "lxml"))
            if not document or not document.body:
                logger.warning("필수 페이지 본문을 찾지 못했습니다 url=%s", normalized)
                return None
            return document.finalize()
        except Exception:
            logger.exception("필수 페이지 수집 실패 url=%s", normalized)
            return None

    def _extract_links(self, current_url: str, soup: BeautifulSoup) -> list[str]:
        links: list[str] = []
        for tag in soup.select("a[href]"):
            candidate = normalize_url(tag.get("href", ""), current_url)
            if self.is_allowed(candidate):
                links.append(candidate)
        links.extend(self._extract_board_page_links(current_url, soup))
        return list(dict.fromkeys(links))

    @staticmethod
    def _course_detail_specs(soup: BeautifulSoup) -> list[dict[str, str]]:
        pattern = re.compile(
            r"jf_detailView\('(?P<year>\d{4})','(?P<semester>[12])','(?P<grade>[1-4])',"
            r"'(?P<course_code>\d+)','(?P<department_code>\d+)'\)"
        )
        specs = []
        seen = set()
        for anchor in soup.select('a[href*="jf_detailView"]'):
            match = pattern.search(anchor.get("href", ""))
            course_name = clean_text(anchor.get_text(" ", strip=True))
            if not match or not course_name:
                continue
            key = (match.group("year"), match.group("semester"), match.group("course_code"))
            if key in seen:
                continue
            seen.add(key)
            specs.append({"course_name": course_name, **match.groupdict()})
        return specs

    def _fetch_course_detail_documents(
        self,
        soup: BeautifulSoup,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[CrawlDocument]:
        """교과목 안내의 JavaScript POST 팝업을 과목별 공식 문서로 수집한다."""
        documents: list[CrawlDocument] = []
        specs = self._course_detail_specs(soup)
        if not self.is_allowed(COURSE_DETAIL_ENDPOINT):
            logger.warning("robots.txt 또는 경로 정책으로 교과목 상세 수집을 건너뜁니다.")
            return documents
        for index, spec in enumerate(specs, start=1):
            try:
                response = self.session.post(
                    COURSE_DETAIL_ENDPOINT,
                    data={
                        "year": spec["year"],
                        "seme": spec["semester"],
                        "shgr": spec["grade"],
                        "sbjtNo": spec["course_code"],
                        "deptCd": spec["department_code"],
                    },
                    timeout=config.CRAWL_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                document = self._parse_course_detail(spec, BeautifulSoup(response.text, "lxml"))
                if document:
                    documents.append(document.finalize())
                if progress:
                    progress(
                        {
                            "visited": index,
                            "queued": len(specs) - index,
                            "documents": len(documents),
                            "depth": 0,
                            "max_depth": self.max_depth,
                            "url": document.source_url if document else COURSE_GUIDE_URL,
                            "percent": min(94, round(index / max(len(specs), 1) * 100)),
                        }
                    )
            except Exception:
                logger.exception(
                    "교과목 상세 수집 실패 course=%s code=%s",
                    spec["course_name"],
                    spec["course_code"],
                )
            time.sleep(self.delay)
        logger.info("교과목 상세 수집 완료: %d/%d", len(documents), len(specs))
        return documents

    @staticmethod
    def _heading_value(area: BeautifulSoup, heading: str) -> str:
        target = next(
            (node for node in area.find_all("h5") if clean_text(node.get_text(" ", strip=True)) == heading),
            None,
        )
        if target is None:
            return ""
        values = []
        for sibling in target.next_siblings:
            if getattr(sibling, "name", None) == "h5":
                break
            text = clean_text(
                sibling.get_text(" ", strip=True)
                if hasattr(sibling, "get_text")
                else str(sibling)
            )
            if text:
                values.append(text)
        return clean_text(" ".join(values))

    def _parse_course_detail(
        self,
        spec: dict[str, str],
        soup: BeautifulSoup,
    ) -> CrawlDocument | None:
        area = soup.select_one("#outlineArea")
        if area is None:
            return None
        overview = self._heading_value(area, "개요")
        media = self._heading_value(area, "매체명")
        topics = []
        detail_topics = []
        table = area.select_one("table")
        if table:
            for row in table.select("tbody tr"):
                cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
                if len(cells) >= 3:
                    if cells[1] and cells[1] not in topics:
                        topics.append(cells[1])
                    if cells[2] and cells[2] not in detail_topics:
                        detail_topics.append(cells[2])
        source_url = f"{COURSE_GUIDE_URL}#course-{spec['course_code']}"
        normalized_item = {
            "course_name": spec["course_name"],
            "grade": f"{spec['grade']}학년",
            "semester": f"{spec['semester']}학기",
            "course_code": spec["course_code"],
            "overview": overview,
            "topics": topics[:15],
            "detail_topics": detail_topics[:15],
            "media": [media] if media else [],
            "source_url": source_url,
        }
        body = "\n".join(
            [
                f"과목명: {spec['course_name']}",
                f"학년/학기: {spec['grade']}학년 {spec['semester']}학기",
                f"과목개요: {overview}",
                f"강의매체: {media}",
                f"주요내용: {', '.join(topics)}",
            ]
        )
        return CrawlDocument(
            title=spec["course_name"],
            category="교과정보 > 교과목안내 > 과목상세",
            body=body,
            source_url=source_url,
            collected_at=datetime.now().astimezone().isoformat(),
            normalized_items=[normalized_item],
        )

    def _extract_board_page_links(self, current_url: str, soup: BeautifulSoup) -> list[str]:
        """JavaScript page_link로만 제공되는 게시판 페이지 URL을 생성한다."""
        current_page = soup.select_one("._curPage")
        total_page = soup.select_one("._totPage")
        page_form = soup.select_one('form[name="pageForm"][action], form[action*="/bbs/cs1/"]')
        if not current_page or not total_page or not page_form:
            return []
        try:
            total = min(int(total_page.get_text(strip=True)), 100)
        except ValueError:
            return []
        action = normalize_url(page_form.get("action", ""), current_url)
        if not self.is_allowed(action):
            return []
        separator = "&" if urlsplit(action).query else "?"
        return [f"{action}{separator}page={page}" for page in range(1, total + 1)]

    def _parse_page(self, url: str, soup: BeautifulSoup) -> CrawlDocument | None:
        page_text = soup.get_text(" ", strip=True)
        title_tag = next(
            (
                soup.select_one(selector)
                for selector in (".view-title", ".board-view-title", ".artclViewTitle", "h1", "title")
                if soup.select_one(selector) is not None
            ),
            None,
        )
        title = clean_text(title_tag.get_text(" ", strip=True) if title_tag else "")
        title = re.sub(r"\s*[-|]\s*컴퓨터과학과.*$", "", title).strip() or "제목 없음"

        breadcrumbs = [
            clean_text(node.get_text(" ", strip=True))
            for node in soup.select(".location a, .breadcrumb a, .path a, .page-title, .sub-title")
        ]
        category = " > ".join(dict.fromkeys(x for x in breadcrumbs if x and x != title))
        if not category:
            category = self._guess_category(url, title)

        attachments = self._extract_attachments(url, soup)
        content = self._select_content(url, soup)
        if content is None:
            return None
        content = BeautifulSoup(str(content), "lxml")
        for tag in content.select(
            "script, style, noscript, iframe, nav, footer, header, input, button, "
            ".hidden, .control, .paging, .page-move, .board-search, .wrap-pop, "
            ".view-util, .view-file, .prev-next, .btn-area"
        ):
            tag.decompose()
        table_headers, table_rows, normalized_items = self._extract_tables(content)
        body = self._structured_text(content)
        if not body:
            return None
        if not normalized_items and ("학과일정" in category or "/4792/" in url):
            normalized_items = extract_schedule_items(body)

        published_at = self._extract_date(page_text)

        return CrawlDocument(
            title=title[:300],
            category=category[:200],
            body=body,
            source_url=url,
            collected_at=datetime.now().astimezone().isoformat(),
            published_at=published_at,
            attachments=list(dict.fromkeys(attachments)),
            table_headers=table_headers,
            table_rows=table_rows,
            normalized_items=normalized_items,
        )

    @classmethod
    def _extract_tables(
        cls,
        content: BeautifulSoup,
    ) -> tuple[list[str], list[list[str]], list[dict[str, Any]]]:
        """HTML 표를 열 병합을 보존한 행렬과 검색/응답용 과목 데이터로 변환한다."""
        best_headers: list[str] = []
        best_rows: list[list[str]] = []
        best_items: list[dict[str, Any]] = []
        for table in content.select("table"):
            matrix, header_flags = cls._table_matrix(table)
            if not matrix:
                continue
            header_count = 0
            for is_header in header_flags:
                if is_header:
                    header_count += 1
                else:
                    break
            if header_count == 0:
                header_count = 1
            width = max(len(row) for row in matrix)
            header_rows = [row + [""] * (width - len(row)) for row in matrix[:header_count]]
            headers = []
            for column in range(width):
                labels = []
                for row in header_rows:
                    label = row[column].strip()
                    if label and label not in labels:
                        labels.append(label)
                headers.append(" / ".join(labels) or f"열{column + 1}")
            rows = [
                row + [""] * (width - len(row))
                for row in matrix[header_count:]
                if any(cell.strip() for cell in row)
            ]
            items = [item for row in rows if (item := cls._normalize_course_row(headers, row))]
            if len(items) > len(best_items) or (not best_rows and len(rows) > len(best_rows)):
                best_headers, best_rows, best_items = headers, rows, items
        return best_headers, best_rows, best_items

    @staticmethod
    def _table_matrix(table: BeautifulSoup) -> tuple[list[list[str]], list[bool]]:
        matrix: list[list[str]] = []
        header_flags: list[bool] = []
        spans: dict[int, tuple[int, str]] = {}
        for tr in table.find_all("tr"):
            row: list[str] = []
            column = 0

            def consume_spans() -> None:
                nonlocal column
                while column in spans:
                    remaining, value = spans[column]
                    row.append(value)
                    if remaining <= 1:
                        spans.pop(column)
                    else:
                        spans[column] = (remaining - 1, value)
                    column += 1

            consume_spans()
            cells = tr.find_all(["th", "td"], recursive=False)
            for cell in cells:
                consume_spans()
                value = clean_text(cell.get_text(" ", strip=True))
                try:
                    colspan = max(1, int(cell.get("colspan", 1)))
                    rowspan = max(1, int(cell.get("rowspan", 1)))
                except (TypeError, ValueError):
                    colspan = rowspan = 1
                for _ in range(colspan):
                    row.append(value)
                    if rowspan > 1:
                        spans[column] = (rowspan - 1, value)
                    column += 1
            consume_spans()
            if row:
                matrix.append(row)
                header_flags.append(bool(cells) and all(cell.name == "th" for cell in cells))
        return matrix, header_flags

    @staticmethod
    def _normalize_course_row(headers: list[str], row: list[str]) -> dict[str, Any] | None:
        pairs = list(zip(headers, row))

        def value_for(*terms: str) -> str:
            return next(
                (value.strip() for header, value in pairs if value.strip() and any(term in header for term in terms)),
                "",
            )

        course_name = value_for("교과목명", "과목명")
        if not course_name or course_name in {"교과목명", "과목명"}:
            return None
        grade_semester = value_for("학년", "학기")
        grade_match = re.search(r"([1-4])\s*[-학년]", grade_semester)
        semester_match = re.search(r"[-학기]\s*([12])", grade_semester)
        if not grade_match:
            grade_match = re.search(r"([1-4])\s*학년", grade_semester)
        if not semester_match:
            semester_match = re.search(r"([12])\s*학기", grade_semester)
        media = [
            header.split("/")[-1].strip()
            for header, value in pairs
            if value.upper() in {"O", "○", "Y"}
            and any(group in header for group in ("강의매체", "수업유형"))
        ]
        evaluations = [
            header.split("/")[-1].strip()
            for header, value in pairs
            if value.upper() in {"O", "○", "Y"} and "평가방법" in header
        ]
        return {
            "course_name": course_name,
            "grade": f"{grade_match.group(1)}학년" if grade_match else "",
            "semester": f"{semester_match.group(1)}학기" if semester_match else "",
            "category": value_for("교과구분", "교과 구분", "구분"),
            "course_code": value_for("교과목코드", "교과목 코드", "과목코드"),
            "credit": value_for("학점"),
            "media": list(dict.fromkeys(media)),
            "evaluation": list(dict.fromkeys(evaluations)),
        }

    @staticmethod
    def _structured_text(content: BeautifulSoup) -> str:
        blocks: list[str] = []
        selectors = "h1, h2, h3, h4, p, li, tr, dt, dd"
        for node in content.select(selectors):
            if node.find_parent(["h1", "h2", "h3", "h4", "p", "li", "tr", "dt", "dd"]):
                continue
            if node.name == "tr":
                cells = [
                    clean_text(cell.get_text(" ", strip=True))
                    for cell in node.find_all(["th", "td"], recursive=False)
                ]
                text = " | ".join(cell for cell in cells if cell)
            else:
                text = clean_text(node.get_text(" ", strip=True))
            if text and (not blocks or blocks[-1] != text):
                blocks.append(text)
        if not blocks:
            return clean_text(content.get_text(" ", strip=True))
        return clean_text("\n\n".join(blocks))

    @staticmethod
    def _select_content(url: str, soup: BeautifulSoup):
        path = urlsplit(url).path
        if path.endswith("/index.do"):
            sections = [
                soup.select_one("#menu3316_obj155"),
                soup.select_one("#multipleContentsDiv_templet_05_20"),
                soup.select_one("#multipleContentsDiv_templet_05_21"),
            ]
            available = [section for section in sections if section is not None]
            if available:
                wrapper = BeautifulSoup("<main></main>", "lxml").main
                for section in available:
                    wrapper.append(BeautifulSoup(str(section), "lxml"))
                return wrapper
        if "artclView.do" in path:
            board_info = soup.select_one(".board-view-info")
            return board_info.find_parent("div", class_="_fnctWrap") if board_info else None
        return (
            soup.select_one("article#_contentBuilder")
            or soup.select_one(".contents")
            or soup.select_one(".sub-content")
            or soup.select_one("#contents")
            or soup.select_one("#content")
            or soup.select_one("main")
        )

    @staticmethod
    def _extract_attachments(url: str, soup: BeautifulSoup) -> list[str]:
        attachments: list[str] = []
        for tag in soup.select("a[href]"):
            href = normalize_url(tag.get("href", ""), url)
            label = tag.get_text(" ", strip=True).lower()
            path = urlsplit(href).path.lower()
            if href and (
                path.endswith(ATTACHMENT_EXTENSIONS)
                or "첨부" in label
                or "download" in href.lower()
                or "filedown" in href.lower()
            ):
                attachments.append(href)
        return list(dict.fromkeys(attachments))

    @staticmethod
    def _extract_date(text: str) -> str:
        for pattern in DATE_PATTERNS:
            match = pattern.search(text or "")
            if match:
                try:
                    year, month, day = map(int, match.groups())
                    return datetime(year, month, day).date().isoformat()
                except ValueError:
                    continue
        return ""

    @staticmethod
    def _guess_category(url: str, title: str) -> str:
        text = f"{url} {title}".lower()
        rules = {
            "공지사항": ("notice", "공지"),
            "학과일정": ("schedule", "schdul", "일정"),
            "교수진": ("professor", "교수"),
            "교육과정": ("curriculum", "교과", "교육과정"),
            "FAQ": ("faq", "자주하는"),
            "Q&A": ("qna", "질문", "상담"),
            "게시판": ("bbs", "board", "게시판"),
            "학과소개": ("intro", "학과소개", "인사말", "연혁"),
        }
        for category, keywords in rules.items():
            if any(keyword in text for keyword in keywords):
                return category
        return "컴퓨터과학과"

    @staticmethod
    def save_snapshot(documents: list[CrawlDocument], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"collected_at": datetime.now().astimezone().isoformat(), "documents": [asdict(d) for d in documents]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class CommunityCrawler:
    """c-knou 공개 게시판의 최근 글을 비공식 보조 지식으로 수집한다."""

    DETAIL_PATH_RE = re.compile(r"^/computer_science/\d+/?$")
    LIST_PATH_RE = re.compile(r"^/computer_science(?:/page/\d+)?/?$")

    def __init__(
        self,
        start_url: str = config.COMMUNITY_START_URL,
        list_pages: int = config.COMMUNITY_LIST_PAGES,
        max_documents: int = config.COMMUNITY_MAX_DOCUMENTS,
        delay: float = config.COMMUNITY_DELAY_SECONDS,
    ) -> None:
        self.start_url = normalize_url(start_url)
        self.list_pages = max(1, list_pages)
        self.max_documents = max(1, max_documents)
        self.delay = max(delay, 1.0)
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
        )
        self.robots = self._load_robots()

    def _load_robots(self) -> RobotFileParser:
        parser = RobotFileParser()
        robots_url = f"https://{config.COMMUNITY_ALLOWED_DOMAIN}/robots.txt"
        parser.set_url(robots_url)
        try:
            response = self.session.get(robots_url, timeout=config.CRAWL_TIMEOUT_SECONDS)
            response.raise_for_status()
            parser.parse(response.text.splitlines())
        except requests.RequestException:
            logger.exception("커뮤니티 robots.txt 확인 실패")
            parser.parse(["User-agent: *", "Disallow: /"])
        return parser

    @staticmethod
    def _is_private_path(path: str) -> bool:
        lowered = (path or "").lower()
        return any(
            token in lowered
            for token in (
                "/login",
                "/logout",
                "/signup",
                "/write",
                "/comment/",
                "/member",
                "/disp",
                "/admin",
                "/download",
            )
        )

    def is_allowed(self, url: str, *, detail_only: bool = False) -> bool:
        normalized = normalize_url(url)
        parts = urlsplit(normalized)
        if (
            not normalized
            or parts.netloc != config.COMMUNITY_ALLOWED_DOMAIN
            or parts.query
            or parts.fragment
            or self._is_private_path(parts.path)
        ):
            return False
        path_allowed = bool(self.DETAIL_PATH_RE.fullmatch(parts.path))
        if not detail_only:
            path_allowed = path_allowed or bool(self.LIST_PATH_RE.fullmatch(parts.path))
        return path_allowed and self.robots.can_fetch(config.USER_AGENT, normalized)

    def _list_urls(self) -> list[str]:
        return [
            self.start_url if page == 1 else f"{self.start_url.rstrip('/')}/page/{page}"
            for page in range(1, self.list_pages + 1)
        ]

    def _detail_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links: list[str] = []
        # 상단 고정 공지는 다른 게시판(/info 등)으로 리다이렉트될 수 있으므로
        # 현재 컴퓨터과학과 목록의 일반 게시물 행(hx)만 대상으로 삼는다.
        for anchor in soup.select('table.bd_lst tr:not(.notice) td.title a.hx[href]'):
            url = normalize_url(anchor.get("href", ""), base_url)
            if self.is_allowed(url, detail_only=True):
                links.append(url)
        return list(dict.fromkeys(links))

    def _parse_detail(self, url: str, soup: BeautifulSoup) -> CrawlDocument | None:
        content = soup.select_one(".rd_body article .rhymix_content, .rd_body article .xe_content")
        title_node = soup.select_one(".rd_hd .np_16px")
        if content is None or title_node is None:
            return None
        title = clean_text(title_node.get_text(" ", strip=True))
        body = clean_text(content.get_text("\n", strip=True))
        if not title or not body:
            return None
        category_node = soup.select_one(".rd_hd .catefl")
        date_node = soup.select_one(".rd_hd .date")
        category_name = (
            clean_text(category_node.get_text(" ", strip=True)).strip("[] ")
            if category_node
            else "일반"
        )
        published_at = KnouCrawler._extract_date(
            date_node.get_text(" ", strip=True) if date_node else ""
        )
        # 외부 커뮤니티 원문을 복제하지 않고 검색 가능한 짧은 공개 요약만 저장한다.
        excerpt = summarize(body, limit=800)
        return CrawlDocument(
            title=title[:300],
            category=f"비공식 커뮤니티 > {category_name}"[:200],
            body=excerpt,
            source_url=url,
            collected_at=datetime.now().astimezone().isoformat(),
            published_at=published_at,
            attachments=[],
            source_type="community",
            source_label="c-knou 컴퓨터과학과 학생 커뮤니티",
        ).finalize()

    def crawl(
        self,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[CrawlDocument]:
        detail_urls: list[str] = []
        for page_number, list_url in enumerate(self._list_urls(), start=1):
            if not self.is_allowed(list_url):
                continue
            try:
                response = self.session.get(list_url, timeout=config.CRAWL_TIMEOUT_SECONDS)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                for url in self._detail_links(BeautifulSoup(response.text, "lxml"), list_url):
                    if url not in detail_urls:
                        detail_urls.append(url)
                if progress:
                    progress(
                        {
                            "visited": page_number,
                            "queued": max(0, self.list_pages - page_number),
                            "documents": 0,
                            "depth": page_number,
                            "max_depth": self.list_pages,
                            "url": list_url,
                            "percent": min(35, round(page_number / self.list_pages * 35)),
                            "source": "community",
                        }
                    )
            except requests.RequestException:
                logger.exception("커뮤니티 목록 수집 실패 url=%s", list_url)
            time.sleep(self.delay)

        documents: list[CrawlDocument] = []
        targets = detail_urls[: self.max_documents]
        for index, url in enumerate(targets, start=1):
            try:
                response = self.session.get(url, timeout=config.CRAWL_TIMEOUT_SECONDS)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                document = self._parse_detail(url, BeautifulSoup(response.text, "lxml"))
                if document:
                    documents.append(document)
                if progress:
                    progress(
                        {
                            "visited": index,
                            "queued": len(targets) - index,
                            "documents": len(documents),
                            "depth": self.list_pages,
                            "max_depth": self.list_pages,
                            "url": url,
                            "percent": 35 + round(index / max(len(targets), 1) * 59),
                            "source": "community",
                        }
                    )
            except requests.RequestException:
                logger.exception("커뮤니티 게시물 수집 실패 url=%s", url)
            except Exception:
                logger.exception("커뮤니티 게시물 파싱 실패 url=%s", url)
            time.sleep(self.delay)
        logger.info(
            "커뮤니티 크롤링 완료: 목록페이지=%d 발견=%d 저장=%d",
            self.list_pages,
            len(detail_urls),
            len(documents),
        )
        return documents
