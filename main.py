"""ComPass FastAPI 애플리케이션."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import config
from chatbot import answer_question
from crawler import KnouCrawler
from notion_client import NotionClient, notion_error_message
from search_index import SearchIndex
from stats import recent_stats, record_interaction_async

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app = FastAPI(title="ComPass", description="Computer + Compass, 학생들의 길잡이", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

index = SearchIndex()
job_lock = threading.Lock()
job_state: dict[str, Any] = {
    "crawl": {"running": False, "message": "대기 중", "result": None},
    "index": {"running": False, "message": "대기 중", "result": None},
}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, str]] = Field(default_factory=list)
    allow_llm: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


def require_admin(password: str | None) -> None:
    if not config.ADMIN_PASSWORD or config.ADMIN_PASSWORD == "change-me":
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD를 먼저 안전한 값으로 설정하세요.")
    if password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="관리자 비밀번호가 올바르지 않습니다.")


def run_crawl_job() -> None:
    if not job_lock.acquire(blocking=False):
        return
    job_state["crawl"] = {"running": True, "message": "홈페이지를 수집하고 있습니다.", "result": None}
    try:
        notion = NotionClient()
        job_state["crawl"]["message"] = "Notion 지식 DB 연결을 확인하고 있습니다."
        notion.validate_database(config.NOTION_KNOWLEDGE_DB_ID, "지식 DB")
        crawler = KnouCrawler()
        documents = crawler.crawl(
            lambda visited, queued, url: job_state["crawl"].update(
                message=f"{visited}개 URL 확인 · 대기 {queued}개 · {url[:80]}"
            )
        )
        notion_result = notion.upsert_many(documents)
        job_state["crawl"] = {
            "running": False,
            "message": "크롤링 및 Notion 적재 완료",
            "result": {"crawled": len(documents), "notion": notion_result},
        }
    except Exception as exc:
        logger.exception("크롤링 작업 실패")
        job_state["crawl"] = {
            "running": False,
            "message": f"실패: {notion_error_message(exc, '지식 DB')}",
            "result": None,
        }
    finally:
        job_lock.release()


def run_index_job() -> None:
    job_state["index"] = {"running": True, "message": "Notion 데이터를 읽고 있습니다.", "result": None}
    try:
        NotionClient().validate_database(config.NOTION_KNOWLEDGE_DB_ID, "지식 DB")
        result = index.rebuild()
        job_state["index"] = {"running": False, "message": "검색 인덱스 생성 완료", "result": result}
    except Exception as exc:
        logger.exception("인덱스 생성 실패")
        job_state["index"] = {
            "running": False,
            "message": f"실패: {notion_error_message(exc, '지식 DB')}",
            "result": None,
        }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@app.post("/api/crawl")
def crawl(background_tasks: BackgroundTasks, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    if job_state["crawl"]["running"]:
        return {"accepted": False, **job_state["crawl"]}
    background_tasks.add_task(run_crawl_job)
    return {"accepted": True, "message": "크롤링 작업을 시작했습니다."}


@app.get("/api/crawl/status")
def crawl_status():
    return job_state["crawl"]


@app.post("/api/index/rebuild")
def rebuild_index(background_tasks: BackgroundTasks, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    if job_state["index"]["running"]:
        return {"accepted": False, **job_state["index"]}
    background_tasks.add_task(run_index_job)
    return {"accepted": True, "message": "인덱스 재생성을 시작했습니다."}


@app.get("/api/index/status")
def index_status():
    return {**index.status(), "job": job_state["index"]}


@app.post("/api/search/test")
def search_test(req: SearchRequest, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    return {"query": req.query, "results": index.search(req.query, req.top_k)}


@app.post("/api/chat")
def chat(req: ChatRequest):
    result = answer_question(
        req.question,
        history=req.history,
        allow_llm=req.allow_llm,
        index=index,
    )
    record_interaction_async(req.question, result)
    return result


@app.get("/api/stats")
def stats(limit: int = 30, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        return {"items": recent_stats(max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"통계 DB 조회 실패: {notion_error_message(exc, '통계 DB')}",
        ) from exc


@app.get("/api/knowledge/recent")
def recent_knowledge(limit: int = 20, x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        return {"items": NotionClient().recent_knowledge(max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"지식 DB 조회 실패: {notion_error_message(exc, '지식 DB')}",
        ) from exc


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "ComPass",
        "meaning": "Computer + Compass",
        "index": index.status(),
        "notion_configured": bool(config.NOTION_TOKEN),
        "llm_provider": config.LLM_PROVIDER,
    }
