from datetime import UTC, datetime

from fastapi.testclient import TestClient

from rag_api.adapters.rest import ui
from rag_api.auth.context import AuthContext, get_auth_context
from rag_api.contracts.common import ApiError, DiscoverableNotice, Status
from rag_api.contracts.documents import DocumentResponse
from rag_api.contracts.qa import Answered
from rag_api.contracts.search import SearchResponse
from rag_api.main import create_app

HTMX = {"HX-Request": "true"}


def make_client():
    app = create_app()
    app.state.active_pack = "test"
    return TestClient(app)


def test_ui_pages_and_assets():
    client = make_client()
    assert 'hx-post="http://testserver/ui/search"' in client.get("/ui").text
    assert 'id="login-form"' in client.get("/ui/demo").text
    assert client.get("/ui/static/js/htmx.min.js").is_success
    assert client.get("/ui/static/images/question-desk-favicon.png").is_success


def test_search_partial_displays_notice_with_no_matches(monkeypatch):
    async def search(body, request, ctx):
        assert body.include_benchmark
        assert ctx.principal_type == "anonymous"
        return SearchResponse(total=0, active_pack="test", documents=[], reference_templates=[],
                              term_cards=[], concepts=[], next_cursor=None, status=Status(), trace_id="test",
                              notices=[DiscoverableNotice(kind="discoverable", title="<문서>", dept_owner="HR",
                                                          dept_name="인사팀", text="협조전 제출")])

    monkeypatch.setattr(ui, "search_documents", search)
    client = make_client()
    response = client.post("/ui/search", data={"query": "출장", "include_benchmark": "true"}, headers=HTMX)
    assert response.is_success
    assert response.headers["X-Upmutok-Fragment"] == "true"
    assert "0건" in response.text and "협조전 제출" in response.text
    assert "&lt;문서&gt;" in response.text


def test_qa_keeps_approval_gate():
    response = make_client().post("/ui/qa", data={"question": "출장"}, headers=HTMX)
    assert response.status_code == 401
    assert response.headers["X-Upmutok-Fragment"] == "true"
    assert "로그인이 필요" in response.text


def test_qa_renders_header_and_external_evidence(monkeypatch):
    async def active_user():
        return AuthContext(principal_type="user", status="ACTIVE", active_pack="test",
                           snapshot_at=datetime.now(UTC))

    async def answer(body, request, ctx):
        return Answered(
            header="외부 자료를 기준으로 안내합니다.", answer=[{"text": "해당 양식을 참고하세요.", "citations": ["c1"]}],
            evidence=[{"chunk_id": "c1", "doc_id": "FORM", "title": "양식", "dept_owner": "HR", "dept_name": "인사팀",
                       "section_path": "표준 양식", "clause_label": "표준 양식", "text": "양식 본문",
                       "source_tier": 2, "source_badge": None, "version_id": "1d78f4dc-51c0-4449-a786-bde813339735",
                       "version_tag": "v1", "effective_from": "2026-11-24", "version_badge": None}],
            conflict_clauses=[], notices=[], status=Status(), trace_id="test",
        )

    monkeypatch.setattr(ui, "qa_answers", answer)
    client = make_client()
    client.app.dependency_overrides[get_auth_context] = active_user
    response = client.post("/ui/qa", data={"question": "양식"}, headers=HTMX)
    assert response.is_success
    assert 'class="source-banner"' in response.text and "외부 자료 · 사내 규정 아님" in response.text
    assert 'href="#evidence-1"' in response.text and "chunk_id=c1" in response.text


def test_document_highlights_citation_as_text(monkeypatch):
    async def document(request, response, ctx, doc_id, version_tag, chunk_id):
        assert doc_id == "TRAVEL" and chunk_id == "c1"
        return DocumentResponse(doc_id=doc_id, title="출장비 규정", doc_type="official_policy", source_tier=1,
                                dept_owner="HR", dept_name="인사팀", version_id="1d78f4dc-51c0-4449-a786-bde813339735",
                                version_tag="v1", effective_from="2026-11-24", effective_to=None, is_current=True,
                                version_badge=None, body="제3조 <증빙> 제출", versions=[], workflow=None, trace_id="test",
                                location={"chunk_id": chunk_id, "found": True, "section_path": "제3조", "text": "<증빙>"})

    monkeypatch.setattr(ui, "get_document", document)
    response = make_client().get("/ui/documents/TRAVEL?chunk_id=c1", headers=HTMX)
    assert response.is_success
    assert '<mark id="ui-citation" tabindex="-1">&lt;증빙&gt;</mark>' in response.text


def test_document_access_denial_displays_cooperation_guide(monkeypatch):
    async def document(*args):
        raise ApiError(403, "ACL_DENIED", "열람 권한이 없는 문서입니다.",
                       {"dept_name": "인사팀", "text": "협조전을 제출해 주세요."})

    monkeypatch.setattr(ui, "get_document", document)
    response = make_client().get("/ui/documents/TRAVEL", headers=HTMX)
    assert response.status_code == 403
    assert "인사팀" in response.text and "협조전을 제출해 주세요." in response.text
