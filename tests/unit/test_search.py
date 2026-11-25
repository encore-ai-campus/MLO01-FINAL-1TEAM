"""검색(검색 API 계획 4.1 검색 묶기·계약). DB·임베딩 없이 가짜 엔진으로."""
import asyncio
import dataclasses
import uuid
from datetime import UTC, date, datetime

import pytest

from rag_api.auth.context import AuthContext
from rag_api.contracts.common import ApiError
from rag_api.contracts.search import SearchRequest
from rag_api.knowledge_engine.types import (DiscoverHint, DocMeta, MatchedConcept, RetrievalResult, RetrievedChunk,
                                            Status, Trace)
from rag_api.main import create_app
from rag_api.rules import values
from rag_api.search import service
from rag_api.settings import ontology
from rag_api.settings.ontology import Concept, OntologyDictionary
from rag_api.shared.config import Config
from rag_api.shared.tokenize import ConceptTerm, load_dictionary
from asgi_client import post

TODAY = date(2026, 11, 22)
DEPTS = {"FIN": "재무·회계·세무", "HR": "인사·노무", "COMMON": "전사 공통"}
NOW = datetime(2026, 11, 22, tzinfo=UTC)
ANON = AuthContext(principal_type="anonymous", active_pack="it_service", snapshot_at=NOW)
HR_USER = AuthContext(principal_type="user", user_id=uuid.UUID(int=1), status="ACTIVE", dept_code="HR",
                      role="EMPLOYEE", active_pack="it_service", snapshot_at=NOW)


def chunk(doc_id, n, doc_type="official_policy", effective=date(2026, 1, 1), content=None, dept="FIN"):
    meta = DocMeta(doc_id, f"{doc_id} 제목", "v1.0", doc_type, 2 if doc_type in ("standard_template", "benchmark_reference") else 1, dept,
                   effective, uuid.uuid5(uuid.NAMESPACE_URL, doc_id))
    return RetrievedChunk(f"{doc_id}::v1.0::c{n:03d}", f"제{n}조", content or f"{doc_id} 본문 {n}", [], meta,
                          bm25=3.2, cosine=0.8, rrf=0.03, final=0.036)


class FakeEngine:
    def __init__(self, chunks, hints=(), degraded=False, matched=()):
        self.cfg = Config(database_url="x")
        self.chunks, self.hints, self.degraded, self.matched = chunks, list(hints), degraded, list(matched)
        self.queries, self.hint_calls = [], 0

    async def retrieve(self, q, ctx):
        self.queries.append(q)
        status = Status(True, ("EMBEDDING_UNAVAILABLE",)) if self.degraded else Status()
        return RetrievalResult(self.chunks, self.matched, Trace("v1.0", (), 0, 0), status, 0.9)

    async def discoverable_hints(self, q, ctx):
        self.hint_calls += 1
        return self.hints


@pytest.fixture(autouse=True, scope="module")
def dictionary():
    concepts = (Concept("CON-XX-901", "지출결의서", ("지결", "NDA 검토"), (), "돈을 쓰기 전 결재", "base", "FIN"),
                Concept("CON-XX-913", "정산", (), (), "전사 정산 해설", "base", "COMMON"),
                Concept("CON-XX-912", "정산", (), (), "인사 정산 해설", "base", "HR"),
                Concept("CON-XX-911", "정산서", ("정산",), (), "재무 정산 해설", "base", "FIN"),
                Concept("CON-XX-921", "지결", (), (), "다른 팩", "ecommerce", "CX"))
    load_dictionary([ConceptTerm(c.canonical_term, c.synonyms) for c in concepts], "v1.0")
    ontology._current = OntologyDictionary("v1.0:5", concepts)


def search(engine, ctx=ANON, today: date | None = TODAY, rules=values.load(), **req):
    return asyncio.run(service.search_documents(engine, DEPTS, rules, SearchRequest(**{"query": "정산 기준", **req}),
                                                ctx, "srch-test", today))


def test_group_order_snippets_and_badge():
    long = "가" * 300
    # 순위가 doc_id·chunk_id 알파벳 순서와 다름
    engine = FakeEngine([chunk("B", 2, content=long), chunk("C", 1, effective=date(2026, 11, 23)), chunk("B", 1),
                         chunk("B", 3), chunk("A", 1, effective=TODAY)])
    r = search(engine)
    assert [d.doc_id for d in r.documents] == ["B", "C", "A"]  # 문서 순서 = 첫 청크 순위
    b = r.documents[0]
    assert [s.chunk_id for s in b.snippets] == ["B::v1.0::c002", "B::v1.0::c001"]  # 문서당 2개, 순위대로
    assert b.snippets[0].text == "가" * 200 and b.snippets[0].section_path == "제2조"
    assert [d.version_badge for d in r.documents] == [None, "scheduled", None]  # 시행일이 오늘보다 뒤일 때만
    assert (b.dept_name, r.total, r.active_pack, r.trace_id) == ("재무·회계·세무", 3, "it_service", "srch-test")


@pytest.mark.parametrize("utc, badges", [
    (datetime(2026, 11, 22, 14, 59, tzinfo=UTC), ["scheduled", "scheduled"]),  # KST 11-22 23:59
    (datetime(2026, 11, 22, 15, 0, tzinfo=UTC), [None, "scheduled"]),          # KST 11-23 00:00, UTC 는 아직 11-22
    (datetime(2026, 11, 22, 23, 59, tzinfo=UTC), [None, "scheduled"])])        # KST 11-23 08:59, UTC 는 아직 11-22
def test_badge_uses_korean_date(monkeypatch, utc, badges):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz)
    monkeypatch.setattr(service, "datetime", Frozen)
    engine = FakeEngine([chunk("A", 1, effective=date(2026, 11, 23)), chunk("B", 1, effective=date(2026, 11, 24))])
    r = search(engine, today=None)  # 오늘을 주지 않으면 서비스가 정함
    assert [d.version_badge for d in r.documents] == badges


def test_templates_separate_max_5_and_not_counted():
    chunks = [chunk(f"STD-{i}", 1, "standard_template") for i in range(7)] + [chunk("A", 1), chunk("B", 1)]
    r = search(FakeEngine(chunks))
    assert [t.doc_id for t in r.reference_templates] == [f"STD-{i}" for i in range(5)]
    assert [d.doc_id for d in r.documents] == ["A", "B"] and r.total == 2


def test_benchmark_references_go_to_their_own_list_with_their_own_cap_so_templates_are_not_pushed_out():
    """외부 참고 사례(benchmark_reference)는 documents·total·reference_templates 에 섞이지 않고 reference_cases 로, 순위대로 따로 상한(5)까지."""
    cases = [chunk(f"BM-{i}", 1, "benchmark_reference") for i in range(7)]
    chunks = [chunk("A", 1), *cases[:6], chunk("STD-0", 1, "standard_template"), chunk("B", 1), chunk("STD-1", 1, "standard_template"),
              cases[6]]  # 외부 사례가 양식보다 앞서 많이 있어도
    r = search(FakeEngine(chunks), include_benchmark=True)
    assert [d.doc_id for d in r.documents] == ["A", "B"] and r.total == 2
    assert [t.doc_id for t in r.reference_templates] == ["STD-0", "STD-1"]  # 양식은 밀리지 않음
    assert [t.doc_id for t in r.reference_cases] == [f"BM-{i}" for i in range(5)]  # 사례는 순위대로 5건까지(BM-5·6 은 상한 밖)
    assert {t.doc_type for t in r.reference_templates} == {"standard_template"} and {t.doc_type for t in r.reference_cases} == {"benchmark_reference"}


def test_without_benchmark_the_response_is_the_same_and_reference_cases_is_empty():
    chunks = [chunk("A", 1), chunk("STD-0", 1, "standard_template"), chunk("B", 1)]
    r = search(FakeEngine(chunks))  # include_benchmark 기본 false: 엔진은 외부 사례를 주지 않음
    assert (r.reference_cases, [d.doc_id for d in r.reference_templates], [d.doc_id for d in r.documents]) == ([], ["STD-0"], ["A", "B"])


def test_benchmark_references_do_not_shift_pages_or_totals():
    chunks = [chunk(f"D{i:02d}", 1) for i in range(12)] + [chunk("BM", 1, "benchmark_reference")]
    p1 = search(FakeEngine(chunks), limit=10, include_benchmark=True)
    p2 = search(FakeEngine(chunks), limit=10, include_benchmark=True, cursor=p1.next_cursor)
    assert (p1.total, len(p1.documents), p1.next_cursor, [d.doc_id for d in p1.reference_templates]) == (12, 10, "10", [])
    assert [d.doc_id for d in p1.reference_cases] == ["BM"]
    assert ([d.doc_id for d in p2.documents], p2.next_cursor, p2.reference_templates, p2.reference_cases) == (["D10", "D11"], None, [], [])  # 둘째 쪽엔 없음


@pytest.mark.parametrize("doc_types, sent", [
    ([], ()), (["official_policy"], ("official_policy", "standard_template", "benchmark_reference")),
    (["standard_template", "ojt_guide"], ("standard_template", "ojt_guide", "benchmark_reference"))])
def test_doc_types_always_include_templates(doc_types, sent):
    engine = FakeEngine([])
    search(engine, doc_types=doc_types, dept_filter=["FIN"], include_benchmark=True)
    q = engine.queries[0]
    assert (q.text, q.strategy, q.doc_types, q.dept_filter, q.include_benchmark) == (
        "정산 기준", "hybrid", sent, ("FIN",), True)


def test_cursor_pages():
    chunks = [chunk(f"D{i:02d}", 1) for i in range(25)] + [chunk("STD", 1, "standard_template")]
    engine = FakeEngine(chunks, matched=[MatchedConcept("CON-XX-913", "정산", (), "")])
    p1 = search(engine, query="정산", limit=10)
    assert [d.doc_id for d in p1.documents] == [f"D{i:02d}" for i in range(10)] and p1.total == 25
    assert p1.reference_templates and p1.term_cards and p1.concepts and p1.next_cursor
    p2 = search(engine, query="정산", limit=10, cursor=p1.next_cursor)
    assert [d.doc_id for d in p2.documents] == [f"D{i:02d}" for i in range(10, 20)] and p2.total == 25
    assert (p2.reference_templates, p2.reference_cases, p2.term_cards, p2.concepts, p2.notices) == ([], [], [], [], [])  # 첫 쪽에만
    p3 = search(engine, query="정산", limit=10, cursor=p2.next_cursor)
    assert [d.doc_id for d in p3.documents] == [f"D{i:02d}" for i in range(20, 25)] and p3.next_cursor is None
    assert len(engine.queries) == 3  # 쪽마다 다시 찾음
    assert (p1.next_cursor, p2.next_cursor) == ("10", "20")  # 다음 쪽 시작 위치
    for bad in ("0", "-5", "abc", "1.5", " 3", "²", "MTA="):  # 0, 음수, 숫자 아님, 위 첨자, 예전 base64
        with pytest.raises(ApiError) as e:
            search(engine, cursor=bad)
        assert (e.value.http_status, e.value.code) == (422, "VALIDATION_FAILED")


def test_cursor_past_end_and_exact_multiple(app):
    engine = FakeEngine([chunk(f"D{i:02d}", 1) for i in range(20)])
    p2 = search(engine, limit=10, cursor=search(engine, limit=10).next_cursor)
    assert len(p2.documents) == 10 and p2.next_cursor is None  # 문서 수가 limit 의 배수면 마지막 쪽에서 끝
    r = post(app, SEARCH, {"query": "정산", "cursor": "200"})  # 범위 밖은 오류가 아니라 빈 쪽
    assert r.status_code == 200 and (r.json()["documents"], r.json()["total"], r.json()["next_cursor"]) == ([], 1, None)


def test_term_cards_exact_match_and_order():
    engine = FakeEngine([chunk("A", 1)])
    cards = lambda q, ctx=ANON: [(c.concept_id, c.matched_text) for c in search(engine, ctx, query=q).term_cards]
    assert cards("지결") == [("CON-XX-901", "지결")]  # 동의어. ecommerce 팩 개념은 활성 팩 밖
    assert cards(" 지결 ") == cards("지결") and cards("ＮＤＡ　검토") == cards("nda검토") == [("CON-XX-901", "NDA 검토")]
    assert cards("지결 기한") == cards("지출") == []  # 검색어 전체가 같을 때만
    # 비로그인: 전사 먼저, 그다음 부서 해설(concept_id 순). HR 직원: 자기 부서 먼저, 그다음 전사
    assert cards("정산") == [("CON-XX-913", "정산"), ("CON-XX-911", "정산"), ("CON-XX-912", "정산")]
    assert cards("정산", HR_USER) == [("CON-XX-912", "정산"), ("CON-XX-913", "정산"), ("CON-XX-911", "정산")]
    pending = HR_USER.model_copy(update={"status": "PENDING"})  # 승인 전은 부서가 있어도 전사 먼저
    assert cards("정산", pending) == cards("정산")
    card = search(engine, query="지결").term_cards[0]
    assert (card.canonical_term, card.definition, card.dept_owner) == ("지출결의서", "돈을 쓰기 전 결재", "FIN")


def test_notices_on_first_page_with_or_without_documents():
    hint = DiscoverHint("T-HR", "임원 보수 지급 기준", "인사·노무(기본)", "HR")
    engine = FakeEngine([chunk("STD", 1, "standard_template")], hints=[hint])  # 양식만 있고 documents 0건
    r = search(engine, ctx=HR_USER)
    assert [n.model_dump() for n in r.notices] == [{
        "kind": "discoverable", "title": "임원 보수 지급 기준", "dept_owner": "HR", "dept_name": "인사·노무",
        "text": "본 문서는 인사·노무 열람 제한 규정입니다. 열람이 필요한 경우 사내 그룹웨어에서 인사·노무 앞으로 "
                "'문서 열람 협조전'을 신청해 주시기 바랍니다."}]  # 표시 이름은 활성 팩 부서 표
    r = search(engine, ctx=HR_USER, rules=dataclasses.replace(values.load(), cooperation_request="{dept_name} 에 문의"))
    assert r.notices[0].text == "인사·노무 에 문의"  # 문구는 받은 규칙(rules.yaml)에서
    # 7차 회신 2.3절: 볼 수 있는 문서가 나와도 존재 공개 안내를 함께. 안내 문서는 결과에 섞지 않음
    engine = FakeEngine([chunk("A", 1), chunk("B", 1)], hints=[hint])
    p1 = search(engine, ctx=HR_USER, limit=1)
    assert [d.doc_id for d in p1.documents] == ["A"] and [n.title for n in p1.notices] == ["임원 보수 지급 기준"]
    assert "T-HR" not in p1.model_dump_json() and engine.hint_calls == 1
    p2 = search(engine, ctx=HR_USER, limit=1, cursor=p1.next_cursor)
    assert [d.doc_id for d in p2.documents] == ["B"] and p2.notices == [] and engine.hint_calls == 1  # 첫 쪽에만


def test_status_concepts_and_no_scores():
    engine = FakeEngine([chunk("A", 1)], degraded=True, matched=[MatchedConcept("CON-XX-901", "지출결의서", ("지결",), "설명")])
    r = search(engine)
    assert r.status.model_dump() == {"complete": True, "degraded": True, "reason_codes": ["EMBEDDING_UNAVAILABLE"]}
    assert [c.model_dump() for c in r.concepts] == [{"concept_id": "CON-XX-901", "canonical_term": "지출결의서"}]
    text = r.model_dump_json()
    assert not any(k in text for k in ("bm25", "cosine", "rrf", "final", "lexemes", "keyword", "trace\"", "3.2", "0.036"))


@pytest.fixture
def app():
    app = create_app()  # lifespan 대신 상태를 직접 둠
    app.state.cfg = Config(database_url="x")
    app.state.engine, app.state.departments, app.state.active_pack = FakeEngine([chunk("A", 1)]), DEPTS, "it_service"
    app.state.rules = values.load()
    return app


SEARCH = "/api/v1/search/documents"


def test_route_anonymous_and_response_shape(app):
    r = post(app, SEARCH, {"query": "정산 기준"})
    body = r.json()
    assert r.status_code == 200 and list(body) == ["total", "active_pack", "documents", "reference_templates", "reference_cases", "term_cards",
                                                   "concepts", "notices", "next_cursor", "status", "trace_id"]
    assert body["trace_id"].startswith("srch-") and body["documents"][0]["doc_id"] == "A"
    assert post(app, SEARCH, {"query": "정산", "pack": "ecommerce"}).json()["active_pack"] == "it_service"  # 팩은 요청으로 받지 않음
    assert set(body["documents"][0]) == {"doc_id", "title", "dept_owner", "dept_name", "doc_type", "source_tier", "version_id",
                                         "version_tag", "effective_from", "version_badge", "snippets"}


@pytest.mark.parametrize("body", [
    {"query": ""}, {"query": "가" * 201}, {"query": "a\u0000"}, {"limit": 0}, {"limit": 31}, {"doc_types": ["memo"]},
    {"dept_filter": ["FIN\u0000"]}, {"cursor": "x" * 65}, {"query": 3}, {"query": None}, {"query": "   "},
    {"query": "\u3000\t"}, {"dept_filter": ["FIN"] * 11}, {"dept_filter": ["x" * 17]}, {"doc_types": ["ojt_guide"] * 5}])
def test_bad_request_422(app, body):
    r = post(app, SEARCH, {"query": "정산", **body})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "VALIDATION_FAILED")
    assert r.json()["error"]["trace_id"].startswith("srch-")


def test_query_trimmed_and_limits_inclusive(app):
    r = post(app, SEARCH, {"query": " !!! ", "dept_filter": ["x" * 16] * 10, "doc_types": ["ojt_guide"] * 4})
    assert r.status_code == 200  # 기호만 있는 검색어와 상한 그대로는 받음
    assert app.state.engine.queries[-1].text == "!!!"  # 앞뒤 공백은 지우고 넘김
