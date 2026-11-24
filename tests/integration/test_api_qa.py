"""AI 답변을 HTTP 로(qa 계획 4.2). 시드 계정을 요청자로 넣어(requester.py) 질문. 앱은 conftest 의 `api`(가짜 임베더·가짜 LLM)로 띄움.
볼 수 있는 문서는 test_retrieve 의 표."""
import json
import socket

import pytest
from requester import headers
from test_api_search import REQUESTERS, _requester  # noqa: F401  (autouse 고정물)
from test_retrieve import ALL_EMP

from rag_api.shared.llm import LLMClient

QUESTION = "출장비 정산 기준을 알려 주세요"


async def ask(client, email, **body):
    return await client.post("/api/v1/qa/answers", json={"question": QUESTION, **body}, headers=headers(email))


@pytest.mark.parametrize("email, status, code", [
    (None, 401, "UNAUTHENTICATED"), ("pending@company.com", 403, "ACCOUNT_PENDING"),
    ("revision@company.com", 403, "ACCOUNT_PENDING"), ("rejected@company.com", 403, "ACCOUNT_REJECTED")])
def test_only_active_accounts(api, email, status, code):
    async def run(client, conn):
        for body in ({}, {"question": ""}):  # 본문이 틀려도 계정 확인이 먼저
            r = await ask(client, email, **body)
            assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    api(run)


def test_answered_within_acl_per_requester(api):
    async def run(client, conn):
        for who in ("hr", "fin", "pln_sls", "admin"):
            email, visible = REQUESTERS[who]
            r = await ask(client, email)
            body = r.json()
            assert r.status_code == 200 and body["result"] == "answered", who
            assert body["status"] == {"complete": True, "degraded": False, "reason_codes": []}, who
            docs = {e["doc_id"] for e in body["evidence"]}
            assert docs and docs <= visible, who  # 근거에 볼 수 없는 문서 0건
            assert all(e["chunk_id"].split("::")[0] == e["doc_id"] for e in body["evidence"]), who
            assert {c for s in body["answer"] for c in s["citations"]} == {e["chunk_id"] for e in body["evidence"]}, who
            assert body["trace_id"].startswith("qa-")
    api(run)


def test_no_llm_address_is_stage1_fallback(api, monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.delenv("LLM_MODEL")

    async def run(client, conn):
        body = (await ask(client, "fin@company.com")).json()
        assert body["result"] == "fallback_excerpts"
        assert body["status"] == {"complete": True, "degraded": True, "reason_codes": ["LLM_UNAVAILABLE"]}
        assert {e["doc_id"] for e in body["excerpts"]} <= REQUESTERS["fin"][1]
    api(run)


def test_llm_timeout_is_200_fallback_not_503(api, monkeypatch):
    """실제 LLM 클라이언트로, 연결은 받고 응답하지 않는 소켓에 보냄. TimeoutError(OSError 하위)가 main 의 503 으로 새지 않음."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{s.getsockname()[1]}/v1")
        monkeypatch.setenv("LLM_TIMEOUT_S", "0.3")

        async def run(client, conn):
            r = await ask(client, "fin@company.com")
            assert r.status_code == 200 and r.json()["result"] == "fallback_excerpts"
            assert r.json()["status"] == {"complete": True, "degraded": True, "reason_codes": ["LLM_TIMEOUT"]}
        api(run, llm=LLMClient)


def test_insufficient_evidence_with_ojt_questions(api):
    async def run(client, conn):
        body = (await ask(client, "fin@company.com", question="회사 근처 점심 맛집 추천해 줘")).json()
        ojt = json.loads(await conn.fetchval("SELECT ojt_questions::text FROM departments WHERE dept_code = 'FIN'"))
        assert body["result"] == "insufficient_evidence" and body["message"] == "질문하신 내용과 일치하는 사내 규정 근거를 찾지 못했습니다."
        assert body["suggested_questions"] == ojt and ojt  # 요청자 주 부서(FIN)의 OJT 추천 질문
        assert body["status"]["degraded"] is False
    api(run)


def test_doc_ids_narrow_to_visible_selected_docs(api):
    async def run(client, conn):
        uid = await conn.fetchval("SELECT user_id FROM users WHERE email = 'fin@company.com'")
        before = await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'")
        body = (await ask(client, "fin@company.com", doc_ids=["T-HR", "T-FIN", "없는-문서", "T-LGL-ECO", "T-LGL-OLD", "T-LGL-BAD",
                                                              "T-ECO", "T-BADPTR", "T-BENCH"])).json()
        assert body["result"] == "answered" and {e["doc_id"] for e in body["evidence"]} == {"T-FIN"}
        # 뺀 문서 중 현행·활성 팩인데 볼 수 없는 T-HR 만 감사 로그 ACL_DENIED 한 줄. 없는 ID·다른 팩(T-LGL-ECO 는 부서로도 못 봄)·
        # 현행 포인터 없음(T-LGL-OLD)·포인터가 폐기 버전(T-LGL-BAD)·벤치마크는 남기지 않음(거짓 기록). 응답에는 알리지 않음
        rows = await conn.fetch("SELECT actor_user_id, target_user_id, detail_json::text AS d, ip_address FROM auth_audit_logs"
                                " WHERE action = 'ACL_DENIED' ORDER BY created_at")
        assert len(rows) == before + 1 and rows[-1]["actor_user_id"] == uid and rows[-1]["target_user_id"] is None
        detail = json.loads(rows[-1]["d"])
        assert detail["doc_id"] == "T-HR" and detail["trace_id"] == body["trace_id"] and rows[-1]["ip_address"] == "127.0.0.1"
        assert "ACL_DENIED" not in json.dumps(body)
        # 고른 문서를 모두 볼 수 없으면 찾지 않고 근거 부족. 존재 공개 문서(T-HR)는 제목이 겹치면 안내
        body = (await ask(client, "fin@company.com", question="인사 규정 출장비 정산", doc_ids=["T-HR", "T-RETIRED"])).json()
        assert body["result"] == "insufficient_evidence"
        assert [(n["kind"], n["title"], n["dept_owner"]) for n in body["notices"]] == [("discoverable", "인사 규정", "HR")]
        assert "T-HR" not in json.dumps(body["suggested_questions"], ensure_ascii=False)
    api(run)


def test_doc_ids_cannot_widen_scope(api):
    async def run(client, conn):
        # 볼 수 없는 문서·다른 팩·벤치마크를 골라도 근거는 볼 수 있는 현행 문서만
        body = (await ask(client, "pln@company.com", doc_ids=["T-HR", "T-ECO", "T-BENCH", "T-ALL"])).json()
        assert {e["doc_id"] for e in body["evidence"]} <= ALL_EMP | {"T-LGL"}
        assert {e["doc_id"] for e in body["evidence"]} == {"T-ALL"}
    api(run)


def test_conflict_card_base_dept_and_visible_target(api, monkeypatch):
    """준비 데이터 T-PUB(COMMON) ↔ T-HR·T-PENDING 충돌(qa 계획 3.6). 근거를 T-PUB 로 좁힘. 카드는 상대의 소관 부서가
    기준 부서(질문자·지목)이고 상대를 볼 수 있는 현행 문서일 때만. T-PUB 본문이 짧아 R3 는 다른 시험이 보고 여기서는 통과로 둠."""
    monkeypatch.setattr("rag_api.qa.service.term_stats.insufficient", lambda *a: False)
    card = {"source_doc_id": "T-PUB", "target_doc_id": "T-HR", "target_title": "인사 규정", "clause_pair": "제1조 ↔ 제1조",
            "priority_rule": "부서 최신 세칙 우선 적용"}  # 03B 3.2 의 다섯 필드만(엔진 안쪽 target_dept_owner 는 안 냄)

    async def cards(client, email, question, doc_ids=("T-PUB",)):
        body = (await ask(client, email, question=question, doc_ids=list(doc_ids))).json()
        assert body["result"] == "answered"
        return body["conflict_clauses"]

    async def run(client, conn):
        assert await cards(client, "hr@company.com", "출장비 정산 기준") == [card]  # 질문자 HR. 검수 대기 T-PENDING 은 빠짐
        assert await cards(client, "admin@company.com", "출장비 정산 기준") == []  # 기준 부서 아님
        assert await cards(client, "admin@company.com", "인사팀 세칙의 출장비 정산 기준") == [card]  # 인사 지목
        assert await cards(client, "fin@company.com", "인사팀 세칙의 출장비 정산 기준") == []  # 지목해도 T-HR 을 볼 수 없음
        # 7차 회신 3절: 기준 부서가 아니어도 부서 세칙 T-HR 자체가 근거에 있으면 카드(같은 쌍은 COMMON 문서의 항목 한 장)
        assert await cards(client, "admin@company.com", "출장비 정산 기준", ("T-PUB", "T-HR")) == [card]
    api(run)


def test_answered_also_gets_discoverable_notice(api, monkeypatch):
    """7차 회신 2.3절: 볼 수 있는 문서로 답해도, 제목이 겹치는 볼 수 없는 존재 공개 문서(T-HR) 안내를 함께.
    T-HR 본문·청크는 근거에 섞이지 않음."""
    monkeypatch.setattr("rag_api.qa.service.term_stats.insufficient", lambda *a: False)

    async def run(client, conn):
        body = (await ask(client, "fin@company.com", question="인사 규정 출장비 정산 기준")).json()
        assert body["result"] == "answered" and {e["doc_id"] for e in body["evidence"]} <= REQUESTERS["fin"][1]
        assert [(n["kind"], n["title"], n["dept_owner"]) for n in body["notices"] if n["kind"] == "discoverable"] == [
            ("discoverable", "인사 규정", "HR")]
        assert "T-HR::" not in json.dumps(body, ensure_ascii=False)
    api(run)


def test_acl_denied_audit_skips_benchmark_pack_doc_hidden_by_dept(api):
    """벤치마크 팩 문서는 기본 검색·답변 범위 밖(활성 팩이 아님)이라 부서로 못 봐도 ACL_DENIED 를 남기지 않음(거짓 기록).
    준비 데이터에는 부서로 제한된 벤치마크 문서가 없어 시험이 직접 넣고 지움."""
    async def run(client, conn):
        await conn.execute("INSERT INTO documents (doc_id, title, doc_type, source_tier, knowledge_pack, dept_owner, allowed_depts)"
                           " VALUES ('T-BENCH-HR', '벤치마크 인사', 'benchmark_reference', 2, 'benchmark', 'HR', '{HR}')")
        try:
            vid = await conn.fetchval("INSERT INTO document_versions (doc_id, version_tag, raw_markdown, content_hash, version_status)"
                                      " VALUES ('T-BENCH-HR', 'v1.0', '-', $1, 'active') RETURNING version_id", "0" * 64)
            await conn.execute("UPDATE documents SET current_version_id = $1 WHERE doc_id = 'T-BENCH-HR'", vid)
            before = await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'")
            await ask(client, "fin@company.com", doc_ids=["T-FIN", "T-BENCH-HR"])
            assert await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'") == before
            await ask(client, "fin@company.com", doc_ids=["T-FIN", "T-HR"])  # 같은 요청에 현행 활성 팩 문서가 있으면 그것만 남음
            assert await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'") == before + 1
        finally:
            await conn.execute("UPDATE documents SET current_version_id = NULL WHERE doc_id = 'T-BENCH-HR'")
            await conn.execute("DELETE FROM document_versions WHERE doc_id = 'T-BENCH-HR'")
            await conn.execute("DELETE FROM documents WHERE doc_id = 'T-BENCH-HR'")
    api(run)
