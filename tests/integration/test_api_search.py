"""검색을 HTTP 로(검색 API 계획 4.2). 시드 계정을 요청자로 넣어(requester.py) 검색. 앱은 conftest 의 `api`(가짜 임베더)로 띄움.
볼 수 있는 문서는 test_retrieve 의 표(시험 문서로 적은 열람 표)를 씀."""
import pytest
from conftest import FakeEmbedder
from requester import headers, install
from test_retrieve import ALL_EMP, PUBLIC

from rag_api.rules import values

COOPERATION_REQUEST = values.load().cooperation_request

# 시험 동안만: 표준 양식 두 건(PUBLIC 하나·FIN 하나)과 SLS 문서 하나(겸직 SLS 인 pln@ 만 볼 수 있음)
TEMP = {"T-PUB": ("standard_template", None), "T-FIN": ("standard_template", None), "T-LGL": (None, ["SLS"])}
REQUESTERS = {  # 이름 → (시드 계정 이메일, 볼 수 있는 현행 문서)
    "anonymous": (None, PUBLIC), "pending": ("pending@company.com", PUBLIC),
    "hr": ("hr@company.com", ALL_EMP | {"T-HR", "T-HR-SAL"}), "fin": ("fin@company.com", ALL_EMP | {"T-FIN"}),
    "pln_sls": ("pln@company.com", ALL_EMP | {"T-LGL"}),
    "admin": ("admin@company.com", ALL_EMP | {"T-HR", "T-HR-SAL", "T-FIN", "T-LGL"}),
}


@pytest.fixture(autouse=True)
def _requester(monkeypatch):
    install(monkeypatch)  # 로그인 대신 요청자 정보를 바꿔 끼워 넣음(requester.py)


async def search(client, email, **body):
    return await client.post("/api/v1/search/documents", json={"limit": 30, **body}, headers=headers(email))


def test_no_document_outside_acl_per_requester(api):
    async def run(client, conn):
        old = {r["doc_id"]: (r["doc_type"], r["allowed_depts"]) for r in await conn.fetch(
            "SELECT doc_id, doc_type, allowed_depts FROM documents WHERE doc_id = ANY($1::text[])", list(TEMP))}
        try:
            for doc_id, (doc_type, allowed) in TEMP.items():
                await conn.execute("UPDATE documents SET doc_type = coalesce($2, doc_type), allowed_depts = coalesce($3, allowed_depts)"
                                   " WHERE doc_id = $1", doc_id, doc_type, allowed)
            for who, (email, visible) in REQUESTERS.items():
                r = await search(client, email, query="회사 규정 출장비 정산 기준")
                assert r.status_code == 200, who
                body = r.json()
                docs = {d["doc_id"] for d in body["documents"]}
                templates = {d["doc_id"] for d in body["reference_templates"]}
                snippet_docs = {s["chunk_id"].split("::")[0] for d in body["documents"] + body["reference_templates"]
                                for s in d["snippets"]}
                # 볼 수 있는 현행 청크가 상한(20) 안이라 볼 수 있는 문서가 모두 나오고, 그 밖은 0건
                assert docs | templates == visible and snippet_docs <= visible, who
                assert templates == visible & {"T-PUB", "T-FIN"} and body["total"] == len(docs), who
                assert all(d["doc_type"] == "standard_template" for d in body["reference_templates"]), who
        finally:
            for doc_id, (doc_type, allowed) in old.items():
                await conn.execute("UPDATE documents SET doc_type = $2, allowed_depts = $3 WHERE doc_id = $1",
                                   doc_id, doc_type, allowed)
    api(run)


def test_benchmark_reference_is_listed_as_a_reference_only_when_asked(api):
    """T-BENCH(외부 참고 사례, benchmark 팩, PUBLIC): include_benchmark 일 때만 나오고, documents·total·reference_templates 가 아니라 reference_cases 로."""
    async def run(client, conn):
        q = "공공기관 출장비 정산 기준 참고 자료"
        off = (await search(client, None, query=q)).json()
        on = (await search(client, None, query=q, include_benchmark=True)).json()
        only = (await search(client, None, query=q, include_benchmark=True, doc_types=["official_policy"])).json()
        ids = lambda r, key: {d["doc_id"] for d in r[key]}  # noqa: E731
        assert off["reference_cases"] == [] and "T-BENCH" not in ids(off, "documents") | ids(off, "reference_templates")  # 요청하지 않으면 어디에도 없음
        assert ids(on, "reference_cases") == {"T-BENCH"} and {d["doc_type"] for d in on["reference_cases"]} == {"benchmark_reference"}
        assert "T-BENCH" not in ids(on, "documents") | ids(on, "reference_templates")
        assert ids(on, "documents") == ids(off, "documents") and on["total"] == off["total"] == len(ids(off, "documents"))
        assert on["reference_templates"] == off["reference_templates"]  # 켜도 표준 양식 목록은 그대로
        assert {d["doc_type"] for d in on["reference_templates"]} <= {"standard_template"}
        assert ids(only, "reference_cases") == {"T-BENCH"}  # 유형을 사규로 좁혀도 참고 목록들은 따로 옴
        assert {d["doc_type"] for d in only["documents"]} == {"official_policy"} and "T-BENCH" not in ids(only, "documents")
    api(run)


@pytest.mark.parametrize("who, dept_filter, query, notices", [
    ("fin", ["HR"], "인사 규정", ["T-HR"]),       # ACTIVE 비ADMIN·0건 → 볼 수 없는 존재 공개 문서
    ("hr", ["FIN"], "재무 규정", ["T-FIN"]),
    ("fin", [], "인사 규정", ["T-HR"]),          # 결과가 있어도 안내(7차 회신 2.3절)
    ("admin", ["CX"], "인사 규정", []),          # ADMIN 은 안내 대상 아님
    ("anonymous", ["HR"], "인사 규정", []),      # 비로그인·승인 전도 아님
    ("pending", ["HR"], "인사 규정", [])])
def test_discoverable_notice_only_for_active_non_admin(api, who, dept_filter, query, notices):
    async def run(client, conn):
        r = (await search(client, REQUESTERS[who][0], query=query, dept_filter=dept_filter)).json()
        names = {row["dept_code"]: row["name"] for row in await conn.fetch(
            "SELECT dept_code, coalesce(pack_labels->>'it_service', default_name) AS name FROM departments")}
        assert (r["total"] == 0) == bool(dept_filter)  # 부서 필터로 0건을 만듦
        assert {d["doc_id"] for d in r["documents"] + r["reference_templates"]} <= REQUESTERS[who][1]  # 안내 문서는 결과에 없음
        assert [n["title"] for n in r["notices"]] == [{"T-HR": "인사 규정", "T-FIN": "재무 규정"}[d] for d in notices]
        for n in r["notices"]:
            assert n == {"kind": "discoverable", "title": n["title"], "dept_owner": n["dept_owner"],
                         "dept_name": names[n["dept_owner"]], "text": COOPERATION_REQUEST.format(dept_name=names[n["dept_owner"]])}
    api(run)


def test_embedding_failure_is_200_degraded(api):
    async def run(client, conn):
        r = await search(client, "fin@company.com", query="출장비 정산 기준")
        body = r.json()
        assert r.status_code == 200 and body["status"] == {"complete": True, "degraded": True,
                                                           "reason_codes": ["EMBEDDING_UNAVAILABLE"]}
        assert body["documents"]  # 키워드 채널만으로 찾음
    api(run, embedder=FakeEmbedder(fail=ConnectionError("임베딩 서버 없음")))


def test_term_card_and_concepts_from_test_dictionary(api):
    async def run(client, conn):
        body = (await search(client, None, query="시험약어")).json()  # 시험용 개념(tests/fixtures/retrieval_docs.json)의 동의어
        assert [(c["concept_id"], c["matched_text"], c["dept_owner"]) for c in body["term_cards"]] == [
            ("CON-XX-901", "시험약어", "FIN")]
        assert {"concept_id": "CON-XX-901", "canonical_term": "시험개념어"} in body["concepts"]
        assert body["status"]["degraded"] is False
    api(run)
