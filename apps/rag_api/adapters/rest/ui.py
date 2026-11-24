"""업무톡 화면과 htmx 조각. 검색, QA, 원문 권한은 기존 API 판정을 따른다."""
from pathlib import Path as FilesystemPath
from typing import Annotated

from fastapi import APIRouter, FastAPI, Path, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from rag_api.adapters.rest.routes import Ctx, get_document, qa_answers, search_documents
from rag_api.auth.context import require_active
from rag_api.contracts.common import ApiError, PgText, trace_id
from rag_api.contracts.qa import QARequest
from rag_api.contracts.search import SearchRequest

UI_ROOT = FilesystemPath(__file__).resolve().parents[3] / "ui"
templates = Jinja2Templates(directory=UI_ROOT / "templates")
router = APIRouter(prefix="/ui", include_in_schema=False, default_response_class=HTMLResponse)


def render(request: Request, name: str, data: dict | None = None, status_code: int = 200):
    partial = request.headers.get("HX-Request") == "true" and name.startswith("partials/")
    context = {"fragment": name, **(data or {})}
    headers = {"Cache-Control": "private, no-store", "Vary": "HX-Request"}
    if partial:
        headers["X-Upmutok-Fragment"] = "true"
    elif name.startswith("partials/"):
        name = "live.html"
    return templates.TemplateResponse(request, name, context, status_code=status_code, headers=headers)


def error_panel(request: Request, exc: ApiError):
    return render(request, "partials/error.html",
                  {"error": exc, "trace_id": trace_id(request)}, exc.http_status)


@router.get("", name="ui-home")
async def home(request: Request):
    return render(request, "live.html")


@router.get("/demo", name="ui-demo")
async def demo(request: Request):
    return render(request, "demo.html")


@router.post("/search", name="ui-search")
async def search_panel(request: Request, ctx: Ctx):
    try:
        async with request.form() as fields:
            body = SearchRequest(
                query=fields.get("query", ""),
                include_benchmark=fields.get("include_benchmark") == "true",
                cursor=fields.get("cursor"),
            )
        result = await search_documents(body, request, ctx)
    except ValidationError:
        return error_panel(request, ApiError(422, "VALIDATION_FAILED", "검색어를 확인해 주세요."))
    except ApiError as exc:
        return error_panel(request, exc)
    return render(request, "partials/search.html", {"result": result, "query": body.query,
                                                   "include_benchmark": body.include_benchmark})


@router.post("/qa", name="ui-qa")
async def qa_panel(request: Request, ctx: Ctx):
    try:
        ctx = await require_active(ctx)
        async with request.form() as fields:
            body = QARequest(question=fields.get("question", ""))
        result = await qa_answers(body, request, ctx)
    except ValidationError:
        return error_panel(request, ApiError(422, "VALIDATION_FAILED", "질문을 확인해 주세요."))
    except ApiError as exc:
        return error_panel(request, exc)
    return render(request, "partials/answer.html", {"result": result})


@router.get("/documents/{doc_id}", name="ui-document")
async def document_panel(
    request: Request,
    response: Response,
    ctx: Ctx,
    doc_id: Annotated[PgText, Path(min_length=1, max_length=64)],
    version_tag: Annotated[PgText, Query(min_length=1, max_length=32)] | None = None,
    chunk_id: Annotated[PgText, Query(min_length=1, max_length=128)] | None = None,
):
    try:
        result = await get_document(request, response, ctx, doc_id, version_tag, chunk_id)
    except ApiError as exc:
        return error_panel(request, exc)
    match = result.location.text if result.location and result.location.found else None
    highlight = result.body.partition(match) if match and match in result.body else None
    return render(request, "partials/document.html", {"document": result, "highlight": highlight})


def mount_ui(app: FastAPI) -> None:
    app.mount("/ui/static", StaticFiles(directory=UI_ROOT / "static"), name="ui-static")
    app.include_router(router)
