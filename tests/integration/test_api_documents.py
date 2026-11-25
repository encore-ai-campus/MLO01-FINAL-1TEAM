"""원문 보기를 HTTP 로(원문보기 구현계획 6절 DB 와 함께). 시드 계정을 요청자로 넣어(requester.py) 엶.
시험 문서(`V-` 로 시작)는 모듈이 시작할 때 넣고 끝날 때 지웁니다: CP1 시험 문서·conftest 는 고치지 않음.

문서 표(개발자가 구현과 따로 적은 기대): 괄호는 allowed_depts·소관·존재 공개
  V-PUB     (PUBLIC)   현행 v2.0, 옛 v1.0(머리말 PUBLIC, 청크 있음), 옛 v0.5(머리말 PUBLIC, 청크 정리됨). 협업 흐름도 있음
  V-ALL     (ALL_EMP)  현행 v3.0, 옛 v2.0(머리말 HR: 옛 판이 더 좁음), 옛 v1.0(머리말 ALL_EMP), 옛 v0.9(머리말 없음)
  V-NARROW  (HR, 공개) 현행 v2.0, 옛 v1.0(머리말 ALL_EMP: 옛 판이 더 넓음)
  V-HR2     (HR)       현행 v1.0. 존재 공개 아님
  V-SLS     (SLS, 공개) 현행 v1.0
  V-FUT     (PUBLIC)   현행 v1.0 시행일 2099-01-01
  V-BENCH   (PUBLIC)   benchmark 팩
  V-BENCH-HR (HR)      benchmark 팩, 존재 공개 아님
  V-ECO     (PUBLIC)   활성 팩 밖(ecommerce)
  V-RETIRED (PUBLIC)   포인터 NULL(급히 내림)
  V-PEND    (PUBLIC)   현행 v1.0, 검수 대기 v2.0
"""
import asyncio
import json
from datetime import date

import asyncpg
import pytest
from conftest import fake_vec
from requester import headers, install

from rag_api.documents.service import drain
from rag_api.rules import values
from rag_api.shared.tokenize import tsv_source

COOPERATION = values.load().cooperation_request
WHO = {"anonymous": None, "pending": "pending@company.com", "revision": "revision@company.com", "rejected": "rejected@company.com",
       "hr": "hr@company.com", "fin": "fin@company.com", "pln_sls": "pln@company.com", "admin": "admin@company.com"}
NOT_ACTIVE = {"anonymous", "pending", "revision", "rejected"}


def hdr(doc_id, allowed):
    """경계 계약 5.1 모양의 버전 머리말(15키, 약 350자). 판 목록이 본문 앞 HEAD_CHARS 만 가져오므로 실제 길이로 시험함. 없으면 빈 글."""
    if not allowed:
        return ""
    return (f"---\ndoc_id: {doc_id}\ntitle: 시험용 규정의 긴 제목 자리 채움 문구 제목#{doc_id}\ndoc_type: official_policy\nsource_tier: 1\nknowledge_pack: it_service\n"
            f"chapter_id: ch99\nchapter_name: 시험 장 이름\ndept_owner: FIN\nallowed_depts: [{allowed}]\neffective_from: 2026-12-01\n"
            "source_org: null\nsource_url: null\nclassification_method: manual_frontmatter\nconfidence_score: 1.0\nreview_reason: null\n---\n")


assert len(hdr("V-X", "ALL_EMP")) >= 340  # 실제 모양 길이(짧은 머리말로는 본문 앞부분 상한 변형을 못 잡음)


def v(tag, status="archived", allowed: str | None = "ALL_EMP", chunks=2, eff="2026-01-01", to=None, current=False, header=True,
      contents=None, raw=None):
    """chunks: 조각 수(내용은 표지 글). contents·raw 를 주면 조각 본문 목록·머리말 뒤 원문을 그대로 씀(합친 조각 시험)."""
    return {"tag": tag, "status": status, "allowed": allowed if header else None, "chunks": chunks, "eff": eff, "to": to,
            "current": current, "contents": contents, "raw": raw}


GRAPH = {"workflow_name": "원문 시험 절차", "current_step": 1, "prev_steps": [],
         "next_steps": [{"step": 2, "doc_id": "V-ALL", "title": "제목#V-ALL", "dept_code": "COMMON", "required_form_id": "V-NARROW", "is_active": False},
                        {"step": 3, "doc_id": "V-HR2", "title": "제목#V-HR2", "dept_code": "HR", "is_active": True},
                        {"step": 4, "doc_id": "V-RETIRED", "title": "제목#V-RETIRED", "dept_code": "COMMON", "is_active": True}],
         "conflicts": [{"target_doc_id": "V-NARROW", "target_title": "제목#V-NARROW", "clause_pair": "제1조 ↔ 제1조", "priority_rule": "규칙"},
                       {"target_doc_id": "V-ALL", "target_title": "제목#V-ALL", "clause_pair": "제2조 ↔ 제1조", "priority_rule": "규칙"}]}

# doc_id → (허용, 소관, 공개, 팩, 문서 유형, 판 목록, 그래프)
DOC_TABLE = {
    "V-PUB": ("PUBLIC", "COMMON", False, "base", "official_policy",
              [v("v2.0", "active", "PUBLIC", 3, "2026-01-01", current=True), v("v1.0", "archived", "PUBLIC", 1, "2025-01-01", "2025-12-31"),
               v("v0.5", "archived", "PUBLIC", 0, "2024-01-01", "2024-12-31"),
               v("v1.5", "archived", "PUBLIC", 1, "2024-06-01", "2024-12-31")], GRAPH),  # 판 이름 순(v1.5 > v1.0)과 시행일 순이 다름
    "V-ALL": ("ALL_EMP", "COMMON", True, "base", "official_policy",
              [v("v3.0", "active", "ALL_EMP", 2, "2026-06-01", current=True), v("v2.0", "archived", "HR", 2, "2026-03-01", "2026-05-31"),
               v("v1.0", "archived", "ALL_EMP", 1, "2026-01-01", "2026-02-28"),
               v("v0.9", "archived", None, 1, "2025-01-01", "2025-12-31", header=False)], None),
    "V-NARROW": ("HR", "HR", True, "base", "official_policy",
                 [v("v2.0", "active", "HR", 2, "2026-06-01", current=True), v("v1.0", "archived", "ALL_EMP", 2, "2026-01-01", "2026-05-31")], None),
    "V-HR2": ("HR", "HR", False, "base", "official_policy", [v("v1.0", "active", "HR", 2, current=True)], None),
    "V-SLS": ("SLS", "SLS", True, "base", "official_policy", [v("v1.0", "active", "SLS", 2, current=True)], None),
    "V-FUT": ("PUBLIC", "COMMON", False, "base", "official_policy", [v("v1.0", "active", "PUBLIC", 1, "2099-01-01", current=True)], None),
    "V-BENCH": ("PUBLIC", "PLN", False, "benchmark", "benchmark_reference", [v("v1.0", "active", "PUBLIC", 1, current=True)], None),
    "V-BENCH-HR": ("HR", "HR", False, "benchmark", "benchmark_reference", [v("v1.0", "active", "HR", 1, current=True)], None),
    # 합친 조각: 정제 스크립트가 `### 제N조` 줄을 버리고 문단을 빈 줄로 이은 조각(c001 = 제1·2조), 본문에는 그 줄이 남아 있음
    "V-MERGE": ("PUBLIC", "COMMON", False, "base", "official_policy",
                [v("v1.0", "active", "PUBLIC", current=True,
                   contents=["목적 문단입니다.\n\n범위 문단입니다.", "기타 문단입니다."],
                   raw="# 제목#V-MERGE\n\n## 제1장 총칙\n\n### 제1조 (목적)\n목적 문단입니다.\n\n### 제2조 (범위)\n범위 문단입니다.\n\n"
                       "### 제3조 (기타)\n기타 문단입니다.\n")], None),
    "V-ECO": ("PUBLIC", "CX", False, "ecommerce", "official_policy", [v("v1.0", "active", "PUBLIC", 1, current=True)], None),
    "V-RETIRED": ("PUBLIC", "COMMON", True, "base", "official_policy", [v("v1.0", "archived", "PUBLIC", 1)], None),
    "V-PEND": ("PUBLIC", "COMMON", False, "base", "official_policy",
               [v("v1.0", "active", "PUBLIC", 1, current=True), v("v2.0", "pending_review", "PUBLIC", 1, "2026-12-01")], None),
}
LIVE_RESTRICTED = {"V-ALL", "V-NARROW", "V-HR2", "V-SLS", "V-BENCH-HR"}  # 현행·활성 팩인데 요청자에 따라 볼 수 없는 문서(감사 대상)
# 요청자 → 열 수 있는 현행 문서(위 표에서 손으로)
OPEN = {
    "anonymous": {"V-PUB", "V-FUT", "V-BENCH", "V-PEND"}, "pending": {"V-PUB", "V-FUT", "V-BENCH", "V-PEND"},
    "revision": {"V-PUB", "V-FUT", "V-BENCH", "V-PEND"}, "rejected": {"V-PUB", "V-FUT", "V-BENCH", "V-PEND"},
    "hr": {"V-PUB", "V-ALL", "V-NARROW", "V-HR2", "V-FUT", "V-BENCH", "V-BENCH-HR", "V-PEND"},
    "fin": {"V-PUB", "V-ALL", "V-FUT", "V-BENCH", "V-PEND"},
    "pln_sls": {"V-PUB", "V-ALL", "V-SLS", "V-FUT", "V-BENCH", "V-PEND"},
    "admin": {"V-PUB", "V-ALL", "V-NARROW", "V-HR2", "V-SLS", "V-FUT", "V-BENCH", "V-BENCH-HR", "V-PEND"},
}
for _open in OPEN.values():
    _open.add("V-MERGE")
GONE = {"V-ECO", "V-RETIRED", "V-NOPE"}  # 누구에게나 404, 감사 없음(활성 팩 밖·현행 아님·없음)


def body_marker(doc_id, tag, i):
    return f"본문#{doc_id}#{tag}#{i}"


async def seed(conn):
    for doc_id, (allowed, owner, discoverable, pack, doc_type, versions, graph) in DOC_TABLE.items():
        await conn.execute(
            "INSERT INTO documents (doc_id, title, doc_type, source_tier, knowledge_pack, dept_owner, allowed_depts, is_discoverable,"
            " ontology_graph_meta) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)", doc_id, f"제목#{doc_id}", doc_type,
            2 if doc_type == "benchmark_reference" else 1, pack, owner, [allowed], discoverable, json.dumps(graph or {}))
        for ver in versions:
            contents = ver["contents"] or [body_marker(doc_id, ver["tag"], i) + " 조항 본문입니다." for i in range(1, ver["chunks"] + 1)]
            raw = hdr(doc_id, ver["allowed"]) + (ver["raw"] or f"# 제목#{doc_id}\n\n판 {ver['tag']}\n\n" + "\n\n".join(
                f"### 제{i}조\n{c}" for i, c in enumerate(contents, 1)))
            vid = await conn.fetchval(
                "INSERT INTO document_versions (doc_id, version_tag, raw_markdown, content_hash, version_status, effective_from, effective_to)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING version_id", doc_id, ver["tag"], raw, "0" * 64, ver["status"],
                date.fromisoformat(ver["eff"]), date.fromisoformat(ver["to"]) if ver["to"] else None)
            for i, c in enumerate(contents, 1):
                section = f"제1장 > 제{i}조"
                await conn.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, version_id, chunk_index, section_path, content, tsv, embedding)"
                    " VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('simple', $7), $8::halfvec)", f"{doc_id}::{ver['tag']}::c{i:03d}",
                    doc_id, vid, i, section, c, tsv_source(f"제목#{doc_id}", section, c), str(fake_vec(c)))
            if ver["current"]:
                await conn.execute("UPDATE documents SET current_version_id = $1 WHERE doc_id = $2", vid, doc_id)


@pytest.fixture(scope="module", autouse=True)
def docs(test_dsn):
    async def run(fn):
        conn = await asyncpg.connect(test_dsn)
        try:
            await fn(conn)
        finally:
            await conn.close()

    async def clean(conn):
        await conn.execute("DELETE FROM auth_audit_logs WHERE detail_json->>'doc_id' LIKE 'V-%'")
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE 'V-%'")
    asyncio.run(run(clean))
    asyncio.run(run(seed))
    yield
    asyncio.run(run(clean))


@pytest.fixture(autouse=True)
def _requester(monkeypatch):
    install(monkeypatch)


async def open_doc(client, who, doc_id, **params):
    return await client.get(f"/api/v1/documents/{doc_id}", params=params, headers=headers(WHO[who]))


async def audit_rows(conn, trace_id):
    await drain()  # 감사는 응답 뒤에 써짐
    return await conn.fetch("SELECT actor_user_id, target_user_id, detail_json::text AS detail, ip_address FROM auth_audit_logs"
                            " WHERE action = 'ACL_DENIED' AND detail_json->>'trace_id' = $1", trace_id)


def title_leaks(text: str, visible: set[str], only_other_than: str = "") -> set[str]:
    """응답 글에 들어 있는, 요청자가 볼 수 없는 문서의 제목·본문 표지."""
    return {d for d in DOC_TABLE if d not in visible and d != only_other_than and (f"제목#{d}" in text or f"본문#{d}#" in text)}


# --- 문서 문: 요청자마다 열리는 문서·403 대 404·감사·누출 ---------------------------------------------------------

def test_open_matrix_status_audit_and_no_leak(api):
    async def run(client, conn):
        for who in WHO:
            actor = None if who == "anonymous" else await conn.fetchval("SELECT user_id FROM users WHERE email = $1", WHO[who])
            for doc_id in [*DOC_TABLE, "V-NOPE"]:
                r = await open_doc(client, who, doc_id)
                label = f"{who} {doc_id}"
                if doc_id in OPEN[who]:
                    expected = 200
                elif doc_id in LIVE_RESTRICTED and DOC_TABLE[doc_id][2] and who not in NOT_ACTIVE:
                    expected = 403  # 존재 공개 ∧ 승인된 계정
                else:
                    expected = 404
                assert r.status_code == expected, label
                trace = r.json().get("trace_id") or r.json()["error"]["trace_id"]
                assert trace.startswith("doc-"), label
                rows = await audit_rows(conn, trace)
                audited = doc_id in LIVE_RESTRICTED and doc_id not in OPEN[who]  # 있는데 볼 수 없음: ADMIN 은 모두 열려 없음
                assert len(rows) == int(audited), label
                if audited:
                    assert (rows[0]["actor_user_id"], rows[0]["target_user_id"], rows[0]["ip_address"]) == (actor, None, "127.0.0.1"), label
                    assert json.loads(rows[0]["detail"]) == {"doc_id": doc_id, "trace_id": trace}, label
                if expected == 200:
                    assert r.json()["doc_id"] == doc_id and r.json()["is_current"] is True, label
                    assert r.headers["cache-control"] == "private, no-store"
                # 볼 수 없는 문서의 제목·본문 표지는 응답 어디에도(성공·오류 모두, 흐름도 포함)
                # V-RETIRED 는 PUBLIC·base 라 흐름도에는 is_active=false 로 남음(볼 수 있음). 직접 열 때는 404 라 제목이 없어야 함
                assert not title_leaks(r.text, OPEN[who] | {"V-RETIRED"}), label
                assert expected == 200 or f"제목#{doc_id}" not in r.text, label
                if expected == 403:
                    err = r.json()["error"]
                    owner = DOC_TABLE[doc_id][1]
                    name = await conn.fetchval("SELECT coalesce(pack_labels->>'it_service', default_name) FROM departments WHERE dept_code = $1", owner)
                    assert set(err) == {"code", "message", "trace_id", "dept_owner", "dept_name", "text"}, label
                    assert (err["code"], err["dept_owner"]) == ("ACL_DENIED", owner) and err["text"] == COOPERATION.format(dept_name=err["dept_name"])
                    assert err["dept_name"] == name, label  # 활성 팩(it_service) 표시 이름
    api(run)


def test_hidden_404_is_indistinguishable_from_missing(api):
    async def run(client, conn):
        for who in ("anonymous", "pending", "fin"):
            missing = (await open_doc(client, who, "V-NOPE")).json()["error"]
            for doc_id in ("V-HR2", "V-ECO", "V-RETIRED"):  # 존재 공개가 아닌 볼 수 없는 문서, 활성 팩 밖, 급히 내림
                r = await open_doc(client, who, doc_id)
                err = r.json()["error"]
                assert r.status_code == 404 and {k: v for k, v in err.items() if k != "trace_id"} == {k: v for k, v in missing.items() if k != "trace_id"}
    api(run)


def test_unapproved_accounts_get_404_even_for_discoverable_docs_but_are_audited(api):
    async def run(client, conn):
        for who in NOT_ACTIVE:
            r = await open_doc(client, who, "V-NARROW")  # 존재 공개 문서
            assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", who
            rows = await audit_rows(conn, r.json()["error"]["trace_id"])
            assert len(rows) == 1 and (rows[0]["actor_user_id"] is None) is (who == "anonymous"), who
    api(run)


def test_gone_docs_are_404_without_audit_even_for_admin(api):
    async def run(client, conn):
        await drain()
        before = await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'")
        for who in WHO:
            for doc_id in ("V-ECO", "V-RETIRED", "V-NOPE"):
                r = await open_doc(client, who, doc_id)
                assert r.status_code == 404, (who, doc_id)
                r = await open_doc(client, who, doc_id, version_tag="v1.0")
                assert r.status_code == 404, (who, doc_id)
        await drain()
        assert await conn.fetchval("SELECT count(*) FROM auth_audit_logs WHERE action = 'ACL_DENIED'") == before
    api(run)


def test_benchmark_doc_opens_but_ecommerce_pack_does_not(api):
    async def run(client, conn):
        r = await open_doc(client, "anonymous", "V-BENCH")
        assert r.status_code == 200 and r.json()["doc_type"] == "benchmark_reference" and r.json()["source_tier"] == 2
        assert (await open_doc(client, "admin", "V-ECO")).status_code == 404
    api(run)


def test_validation_errors_never_audit_or_read(api):
    async def run(client, conn):
        await drain()
        before = await conn.fetchval("SELECT count(*) FROM auth_audit_logs")
        for who in ("anonymous", "fin"):
            for params in ({"chunk_id": "bad"}, {"chunk_id": "V-HR2::v1.0::c001"}, {"version_tag": "v9", "chunk_id": "V-HR2::v1.0::c001"}):
                r = await open_doc(client, who, "V-PUB", **params)
                assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED", (who, params)
            assert (await open_doc(client, who, "V-HR2", chunk_id="V-PUB::v2.0::c001")).status_code == 422  # 경로가 볼 수 없는 문서여도 422 가 먼저
        await drain()
        assert await conn.fetchval("SELECT count(*) FROM auth_audit_logs") == before
    api(run)


# --- 이전 버전: 이중 열람 조건 --------------------------------------------------------------------------------------

VERSIONS = {  # (문서, 요청자) → 판 선택기에 있어야 하는 판
    ("V-PUB", "anonymous"): ["v2.0", "v1.0", "v1.5", "v0.5"], ("V-PUB", "fin"): ["v2.0", "v1.0", "v1.5", "v0.5"],  # 시행일 내림차순
    ("V-ALL", "hr"): ["v3.0", "v2.0", "v1.0"],          # v0.9: 머리말 없음 → 닫힘
    ("V-ALL", "fin"): ["v3.0", "v1.0"],                 # v2.0: 머리말이 HR 만
    ("V-ALL", "pln_sls"): ["v3.0", "v1.0"],
    ("V-ALL", "admin"): ["v3.0", "v2.0", "v1.0", "v0.9"],  # ADMIN 은 머리말을 보지 않음
    ("V-NARROW", "hr"): ["v2.0", "v1.0"], ("V-NARROW", "admin"): ["v2.0", "v1.0"],
    ("V-PEND", "anonymous"): ["v1.0"], ("V-PEND", "admin"): ["v1.0"],  # 검수 대기 v2.0 은 ADMIN 에게도 안 나옴(관리 화면 몫)
}


def test_version_selector_lists_only_openable_versions(api):
    async def run(client, conn):
        for (doc_id, who), tags in VERSIONS.items():
            r = await open_doc(client, who, doc_id)
            assert r.status_code == 200, (doc_id, who)
            versions = r.json()["versions"]
            assert [x["version_tag"] for x in versions] == tags, (doc_id, who)
            assert [x["is_current"] for x in versions] == [True] + [False] * (len(tags) - 1)
    api(run)


def test_direct_open_of_each_version_matches_selector_and_audits_header_blocks(api):
    async def run(client, conn):
        for (doc_id, who), tags in VERSIONS.items():
            for ver in DOC_TABLE[doc_id][5]:
                r = await open_doc(client, who, doc_id, version_tag=ver["tag"])
                label = (doc_id, who, ver["tag"])
                if ver["tag"] in tags:
                    assert r.status_code == 200 and r.json()["version_tag"] == ver["tag"], label
                    assert r.json()["is_current"] is ver["current"], label
                    assert r.json()["body"].startswith(f"# 제목#{doc_id}") and ver["tag"] in r.json()["body"], label
                    continue
                assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", label
                rows = await audit_rows(conn, r.json()["error"]["trace_id"])
                if ver["status"] == "pending_review":
                    assert rows == [], label  # 검수 대기 판은 없는 판과 같음(감사 없음)
                else:
                    assert [json.loads(x["detail"]) for x in rows] == [{"doc_id": doc_id, "version_tag": ver["tag"], "trace_id": r.json()["error"]["trace_id"]}], label
                assert not title_leaks(r.text, set()) and "조항 본문" not in r.text, label
    api(run)


def test_old_version_needs_both_current_doc_and_its_own_header(api):
    async def run(client, conn):
        # 현행이 더 좁은 문서(V-NARROW: HR)의 옛 넓은 판(ALL_EMP): 현행 문 못 넘는 fin 은 열 수 없음(존재 공개라 403)
        for tag in (None, "v1.0"):
            params = {"version_tag": tag} if tag else {}
            r = await open_doc(client, "fin", "V-NARROW", **params)
            assert r.status_code == 403 and "조항 본문" not in r.text, tag
        assert (await open_doc(client, "hr", "V-NARROW", version_tag="v1.0")).status_code == 200  # 둘 다 통과
        # 현행이 더 넓은 문서(V-ALL)의 옛 좁은 판(HR): 문서 문은 넘지만 머리말이 막음
        assert (await open_doc(client, "fin", "V-ALL", version_tag="v2.0")).status_code == 404
        assert (await open_doc(client, "hr", "V-ALL", version_tag="v2.0")).status_code == 200
        # 머리말 없는 옛 판: 닫힘(ADMIN 만)
        assert (await open_doc(client, "hr", "V-ALL", version_tag="v0.9")).status_code == 404
        assert (await open_doc(client, "admin", "V-ALL", version_tag="v0.9")).status_code == 200
    api(run)


def test_previous_version_badge_and_no_workflow(api):
    async def run(client, conn):
        r = (await open_doc(client, "anonymous", "V-PUB", version_tag="v1.0")).json()
        assert (r["is_current"], r["version_badge"], r["workflow"]) == (False, "previous", None)
        assert (r["effective_from"], r["effective_to"]) == ("2025-01-01", "2025-12-31")
        cur = (await open_doc(client, "anonymous", "V-PUB")).json()
        assert (cur["version_badge"], cur["version_tag"]) == (None, "v2.0") and cur["workflow"] is not None
        fut = (await open_doc(client, "anonymous", "V-FUT")).json()
        assert (fut["version_badge"], fut["effective_from"]) == ("scheduled", "2099-01-01")
    api(run)


# --- chunk_id: 조항 위치·과거 인용 -------------------------------------------------------------------------------

def test_chunk_id_locates_clause_in_current_and_old_versions(api):
    async def run(client, conn):
        for who in ("anonymous", "fin"):
            r = (await open_doc(client, who, "V-PUB", chunk_id="V-PUB::v2.0::c002")).json()
            text = body_marker("V-PUB", "v2.0", 2) + " 조항 본문입니다."
            assert r["location"] == {"chunk_id": "V-PUB::v2.0::c002", "found": True, "section_path": "제1장 > 제2조", "text": text}, who
            assert text in r["body"] and r["is_current"] and r["version_badge"] is None
            old = (await open_doc(client, who, "V-PUB", chunk_id="V-PUB::v1.0::c001")).json()  # 과거 인용: 지금은 이전 버전
            assert (old["version_tag"], old["is_current"], old["version_badge"]) == ("v1.0", False, "previous")
            assert old["location"]["found"] is True and old["location"]["text"] in old["body"]
            same = (await open_doc(client, who, "V-PUB", version_tag="v1.0", chunk_id="V-PUB::v1.0::c001")).json()
            assert same == {**old, "trace_id": same["trace_id"]}
    api(run)


def test_merged_chunk_location_text_is_found_in_body(api):
    """합친 조각(c001 = 제1·2조): 조각 본문은 두 문단을 빈 줄로 이은 글이라 본문(사이에 `### 제2조` 줄이 낌)에 그대로 없음 →
    본문에서 첫 문단 시작~마지막 문단 끝을 잘라 돌려줌. 그대로 있는 조각은 그 글."""
    async def run(client, conn):
        r = (await open_doc(client, "anonymous", "V-MERGE", chunk_id="V-MERGE::v1.0::c001")).json()
        merged = "목적 문단입니다.\n\n### 제2조 (범위)\n범위 문단입니다."
        assert r["location"]["text"] == merged and merged in r["body"], r["location"]
        assert r["location"]["found"] and r["location"]["section_path"] == "제1장 > 제1조"
        plain = (await open_doc(client, "anonymous", "V-MERGE", chunk_id="V-MERGE::v1.0::c002")).json()["location"]
        assert plain["text"] == "기타 문단입니다."
    api(run)


def test_pruned_chunk_still_shows_that_version_without_location(api):
    async def run(client, conn):
        for chunk in ("V-PUB::v0.5::c001", "V-PUB::v2.0::c099"):
            r = await open_doc(client, "anonymous", "V-PUB", chunk_id=chunk)
            assert r.status_code == 200, chunk
            body = r.json()
            assert body["location"] == {"chunk_id": chunk, "found": False, "section_path": None, "text": None}, chunk
            assert body["version_tag"] == chunk.split("::")[1] and body["body"].startswith("# 제목#V-PUB"), chunk
        assert (await open_doc(client, "anonymous", "V-PUB", chunk_id="V-PUB::v9.9::c001")).status_code == 404  # 없는 판
    api(run)


def test_chunk_of_hidden_old_version_leaks_nothing(api):
    async def run(client, conn):
        r = await open_doc(client, "fin", "V-ALL", chunk_id="V-ALL::v2.0::c001")  # 머리말이 HR 만
        assert r.status_code == 404 and body_marker("V-ALL", "v2.0", 1) not in r.text and "조항 본문" not in r.text
        r = await open_doc(client, "hr", "V-ALL", chunk_id="V-ALL::v2.0::c001")
        assert r.status_code == 200 and r.json()["location"]["found"] is True
        # 경로의 문서와 chunk_id 의 문서가 다르면 422: 다른 문서의 조각을 경로 문서의 문 뒤에서 읽지 못함
        r = await open_doc(client, "fin", "V-PUB", chunk_id="V-HR2::v1.0::c001")
        assert r.status_code == 422 and "조항 본문" not in r.text
        # 판이 다르면 422: 쿼리의 version_tag 와 chunk_id 의 판이 어긋남
        assert (await open_doc(client, "fin", "V-ALL", version_tag="v3.0", chunk_id="V-ALL::v2.0::c001")).status_code == 422
    api(run)


# --- 협업 흐름도 ---------------------------------------------------------------------------------------------------

def test_workflow_hides_unviewable_targets_per_requester(api):
    async def run(client, conn):
        expected = {  # 요청자 → (단계 문서와 is_active, 서식 있는 단계, 충돌 상대)
            "anonymous": ([("V-RETIRED", False)], [], []), "pending": ([("V-RETIRED", False)], [], []),
            "fin": ([("V-ALL", True), ("V-RETIRED", False)], [], ["V-ALL"]),
            "hr": ([("V-ALL", True), ("V-HR2", True), ("V-RETIRED", False)], ["V-ALL"], ["V-NARROW", "V-ALL"]),
            "admin": ([("V-ALL", True), ("V-HR2", True), ("V-RETIRED", False)], ["V-ALL"], ["V-NARROW", "V-ALL"]),
        }
        for who, (steps, forms, conflicts) in expected.items():
            r = (await open_doc(client, who, "V-PUB")).json()
            w = r["workflow"]
            assert (w["workflow_name"], w["current_step"], w["prev_steps"]) == ("원문 시험 절차", 1, []), who
            assert [(s["doc_id"], s["is_active"]) for s in w["next_steps"]] == steps, who  # is_active 는 현행 포인터로 다시 채움
            assert {s["source_doc_id"] for s in w["next_steps"]} == {"V-PUB"}
            assert [s["doc_id"] for s in w["next_steps"] if s["required_form_id"]] == forms, who
            assert [c["target_doc_id"] for c in w["conflicts"]] == conflicts, who
            assert not title_leaks(json.dumps(r, ensure_ascii=False), OPEN[who] | {"V-RETIRED"}), who
            assert "target_dept_owner" not in json.dumps(r), who
    api(run)


def test_workflow_is_null_for_docs_without_graph(api):
    async def run(client, conn):
        assert (await open_doc(client, "hr", "V-HR2")).json()["workflow"] is None
    api(run)


# --- 검색 결과의 chunk_id 로 원문을 엶(시연 흐름) -----------------------------------------------------------------------

def test_search_hit_chunk_ids_open_in_viewer(api):
    async def run(client, conn):
        for who in ("anonymous", "hr", "admin"):
            r = await client.post("/api/v1/search/documents", json={"query": "조항 본문입니다 시험 규정 정산 기준", "limit": 30}, headers=headers(WHO[who]))
            hits = r.json()["documents"] + r.json()["reference_templates"]
            assert hits and any(d["doc_id"].startswith("V-") for d in hits), who
            for doc in hits:
                for snip in doc["snippets"]:
                    o = await open_doc(client, who, doc["doc_id"], chunk_id=snip["chunk_id"])
                    assert o.status_code == 200, (who, snip["chunk_id"])
                    loc = o.json()["location"]
                    assert loc["found"] is True and loc["section_path"] == snip["section_path"], who
                    assert o.json()["version_id"] == doc["version_id"] and o.json()["is_current"] is True
                    if doc["doc_id"].startswith("V-"):  # 고정물(T-*)의 원문은 '-' 라 글이 없음. 우리 문서는 본문 안에서 그대로 찾힘
                        assert loc["text"] in o.json()["body"], (who, snip["chunk_id"])
                    else:
                        assert loc["text"].startswith(snip["text"]), who
    api(run)


def test_fixture_old_version_without_header_is_closed_except_admin(api):
    """고정물 T-PUB 의 옛 판 v0.9 는 머리말이 없는 원문('-')이라 닫힘."""
    async def run(client, conn):
        for who in ("anonymous", "hr"):
            assert (await open_doc(client, who, "T-PUB", chunk_id="T-PUB::v0.9::c001")).status_code == 404, who
        r = await open_doc(client, "admin", "T-PUB", chunk_id="T-PUB::v0.9::c001")
        assert r.status_code == 200 and r.json()["location"]["found"] is True and r.json()["body"] == "-"
    api(run)
