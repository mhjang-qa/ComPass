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
DATE_PATTERNS = (
    re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),
)
ATTACHMENT_EXTENSIONS = (
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".txt", ".csv", ".jpg", ".jpeg", ".png",
)


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

    def finalize(self) -> "CrawlDocument":
        self.body = clean_text(self.body)
        self.summary = summarize(self.body)
        self.keywords = extract_keywords(f"{self.title} {self.category} {self.body}")
        self.search_text = clean_text(
            " ".join([self.title, self.category, self.summary, " ".join(self.keywords), self.body])
        )
        digest_source = json.dumps(
            {
                "title": self.title,
                "category": self.category,
                "body": self.body,
                "published_at": self.published_at,
                "attachments": sorted(self.attachments),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.content_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        return self


def clean_text(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


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
            sorted(configured_prefixes | {"/cs1", "/sites/cs1", "/bbs/cs1"})
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
        queue = deque([(self.start_url, 0)])
        enqueued: set[str] = {self.start_url}
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

    def _extract_links(self, current_url: str, soup: BeautifulSoup) -> list[str]:
        links: list[str] = []
        for tag in soup.select("a[href]"):
            candidate = normalize_url(tag.get("href", ""), current_url)
            if self.is_allowed(candidate):
                links.append(candidate)
        links.extend(self._extract_board_page_links(current_url, soup))
        return list(dict.fromkeys(links))

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
        for tag in soup.select("script, style, noscript, iframe, nav, footer, header"):
            tag.decompose()

        title_tag = soup.select_one("h1, .view-title, .board-view-title, .artclViewTitle, title")
        title = clean_text(title_tag.get_text(" ", strip=True) if title_tag else "")
        title = re.sub(r"\s*[-|]\s*컴퓨터과학과.*$", "", title).strip() or "제목 없음"

        breadcrumbs = [
            clean_text(node.get_text(" ", strip=True))
            for node in soup.select(".location a, .breadcrumb a, .path a, .page-title, .sub-title")
        ]
        category = " > ".join(dict.fromkeys(x for x in breadcrumbs if x and x != title))
        if not category:
            category = self._guess_category(url, title)

        content = soup.select_one(
            ".artclView, .board-view, .view-content, .contents, #contents, #content, main, .sub-content"
        )
        content = content or soup.body
        if content is None:
            return None
        body = content.get_text("\n", strip=True)
        published_at = self._extract_date(soup.get_text(" ", strip=True))
        attachments = []
        for tag in soup.select("a[href]"):
            href = normalize_url(tag.get("href", ""), url)
            label = tag.get_text(" ", strip=True).lower()
            path = urlsplit(href).path.lower()
            if href and (path.endswith(ATTACHMENT_EXTENSIONS) or "첨부" in label or "download" in href.lower()):
                attachments.append(href)

        return CrawlDocument(
            title=title[:300],
            category=category[:200],
            body=body,
            source_url=url,
            collected_at=datetime.now().astimezone().isoformat(),
            published_at=published_at,
            attachments=list(dict.fromkeys(attachments)),
        )

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
