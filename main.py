"""ComPass FastAPI 애플리케이션."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mhjang-qa.github.io",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

index = SearchIndex()
job_lock = threading.Lock()
job_state: dict[str, Any] = {
    "crawl": {"running": False, "message": "대기 중", "result": None},
    "index": {"running": False, "message": "대기 중", "result": None},
    "notion": {"running": False, "message": "확인 전", "result": None},
}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict[str, str]] = Field(default_factory=list)
    allow_llm: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class CrawlRequest(BaseModel):
    max_depth: int = Field(default=3, ge=0, le=10)


def require_admin(password: str | None) -> None:
    if not config.ADMIN_PASSWORD or config.ADMIN_PASSWORD == "change-me":
        raise HTTPException(status_code=503, detail="ADMIN_PASSWORD를 먼저 안전한 값으로 설정하세요.")
    if password != config.ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="관리자 비밀번호가 올바르지 않습니다.")


def initialize_notion_schemas() -> None:
    if not config.NOTION_TOKEN:
        job_state["notion"] = {
            "running": False,
            "message": "NOTION_TOKEN이 없어 자동 구성을 건너뛰었습니다.",
            "result": None,
        }
        return
    job_state["notion"] = {"running": True, "message": "필수 컬럼 구성 중", "result": None}
    try:
        result = NotionClient().ensure_all_schemas()
        curated_result = NotionClient().upsert_curated_knowledge()
        job_state["notion"] = {
            "running": False,
            "message": "필수 컬럼 구성 완료",
            "result": {**result, "curated": curated_result},
        }
    except Exception as exc:
        logger.exception("Notion DB 자동 구성 실패")
        job_state["notion"] = {
            "running": False,
            "message": notion_error_message(exc),
            "result": None,
        }


@app.on_event("startup")
def startup_initialize_notion() -> None:
    threading.Thread(target=initialize_notion_schemas, daemon=True).start()


def run_crawl_job(max_depth: int) -> None:
    if not job_lock.acquire(blocking=False):
        return
    job_state["crawl"] = {
        "running": True,
        "message": f"크롤링 진행중입니다. Depth {max_depth} 범위를 준비하고 있습니다.",
        "result": None,
        "progress": {
            "percent": 1,
            "visited": 0,
            "queued": 0,
            "documents": 0,
            "depth": 0,
            "max_depth": max_depth,
            "url": "",
        },
    }
    try:
        notion = NotionClient()
        job_state["crawl"]["message"] = "Notion 지식 DB 컬럼을 확인하고 있습니다."
        notion.ensure_knowledge_schema()
        notion.upsert_curated_knowledge()
        crawler = KnouCrawler(max_depth=max_depth)

        def update_crawl_progress(progress: dict[str, Any]) -> None:
            previous_percent = job_state["crawl"].get("progress", {}).get("percent", 0)
            progress["percent"] = max(previous_percent, progress["percent"])
            job_state["crawl"].update(
                message=(
                    "크롤링 진행중입니다. "
                    f"Depth {progress['depth']}/{progress['max_depth']} · "
                    f"방문 {progress['visited']} · 대기 {progress['queued']} · "
                    f"수집 {progress['documents']}"
                ),
                progress=progress,
            )

        documents = crawler.crawl(update_crawl_progress)
        job_state["crawl"].update(
            message="크롤링 완료. Notion DB에 저장하고 있습니다.",
            progress={
                **job_state["crawl"]["progress"],
                "percent": 96,
                "documents": len(documents),
            },
        )
        notion_result = notion.upsert_many(documents)
        job_state["crawl"].update(
            message="Notion 저장 완료. 검색 인덱스를 갱신하고 있습니다.",
            progress={**job_state["crawl"]["progress"], "percent": 98},
        )
        index_result = index.rebuild(notion.knowledge_documents())
        job_state["crawl"] = {
            "running": False,
            "message": "크롤링, 표준화 저장, 검색 인덱스 갱신 완료",
            "result": {
                "crawled": len(documents),
                "notion": notion_result,
                "index": index_result,
                "max_depth": max_depth,
            },
            "progress": {
                **job_state["crawl"]["progress"],
                "percent": 100,
                "documents": len(documents),
            },
        }
    except Exception as exc:
        logger.exception("크롤링 작업 실패")
        job_state["crawl"] = {
            "running": False,
            "message": f"실패: {notion_error_message(exc, '지식 DB')}",
            "result": None,
            "progress": {**job_state["crawl"].get("progress", {}), "percent": 0},
        }
    finally:
        job_lock.release()


def run_index_job() -> None:
    job_state["index"] = {"running": True, "message": "Notion 데이터를 읽고 있습니다.", "result": None}
    try:
        NotionClient().ensure_knowledge_schema()
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
def crawl(
    background_tasks: BackgroundTasks,
    req: CrawlRequest | None = None,
    x_admin_password: str | None = Header(default=None),
):
    require_admin(x_admin_password)
    if job_state["crawl"]["running"]:
        return {"accepted": False, **job_state["crawl"]}
    max_depth = req.max_depth if req else 3
    background_tasks.add_task(run_crawl_job, max_depth)
    return {
        "accepted": True,
        "message": f"크롤링 진행중입니다. Depth {max_depth} 범위를 탐색합니다.",
        "max_depth": max_depth,
    }


@app.get("/api/crawl/status")
def crawl_status():
    return job_state["crawl"]


@app.post("/api/notion/setup")
def setup_notion_databases(x_admin_password: str | None = Header(default=None)):
    require_admin(x_admin_password)
    try:
        result = NotionClient().ensure_all_schemas()
        curated_result = NotionClient().upsert_curated_knowledge()
        return {
            "ok": True,
            "message": "크롤링 지식 DB와 챗봇 통계 DB의 필수 컬럼 구성이 완료되었습니다.",
            "result": {**result, "curated": curated_result},
        }
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Notion DB 구성 실패: {notion_error_message(exc)}",
        ) from exc


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
        "notion_schema": job_state["notion"],
        "llm_provider": config.LLM_PROVIDER,
    }
