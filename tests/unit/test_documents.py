"""원문 보기(원문보기 구현계획 6절 DB 없이). 가짜 풀·가짜 엔진으로 입력 확인·머리말 판정·403/404 가르기·감사·응답 조립.
DB 와 함께인 열람 표·감사 줄은 tests/integration/test_api_documents.py."""
import asyncio
import logging
import signal
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from asgi_client import request
from fastapi import Request

from rag_api.auth.context import AuthContext
from rag_api.contracts.common import ApiError
from rag_api.documents import service
from rag_api.documents.service import header_allows, parse_chunk_id, split_header
from rag_api.knowledge_engine.types import GraphItem
from rag_api import main
from rag_api.main import create_app
from rag_api.rules import values
from rag_api.shared.config import Config

RULES = values.load()
NOW = datetime(2026, 11, 23, tzinfo=UTC)
TODAY = date(2026, 11, 23)
DEPTS = {"FIN": "재무·회계·세무", "HR": "인사·노무", "COMMON": "전사 공통"}
UID = uuid.UUID(int=7)


def ctx(principal="user", status="ACTIVE", dept="FIN", role="EMPLOYEE", extra=()):
    return AuthContext(principal_type=principal, user_id=UID if principal == "user" else None,
                       status=status if principal == "user" else None, dept_code=dept if principal == "user" else None,
                       role=role if principal == "user" else None, extra_depts=list(extra), active_pack="it_service", snapshot_at=NOW)


ANON, FIN, ADMIN = ctx("anonymous"), ctx(), ctx(role="ADMIN")
HEAD = "---\ndoc_id: D\nallowed_depts: [{}]\n---\n# 제목\n"


def version(tag, current=False, allowed="ALL_EMP", eff=date(2026, 1, 1), eff_to=None, head=None):
    return {"title": "문서 제목", "doc_type": "official_policy", "source_tier": 1, "dept_owner": "FIN",
            "version_id": uuid.uuid5(uuid.NAMESPACE_URL, tag), "version_tag": tag, "effective_from": eff, "effective_to": eff_to,
            "is_current": current, "head": HEAD.format(allowed) if head is None else head,
            # 점검 기준(낡은 문서 배지): 오늘(TODAY)보다 6개월 안이라 낡지 않음
            "last_verified_at": None, "current_created_at": datetime(2026, 11, 1)}


class Pool:
    """VERSIONS_SQL → versions, BODY_SQL → bodies[version_id], DENIED_SQL → denied. 감사 INSERT 는 audit 에."""
    def __init__(self, versions=(), bodies=None, denied=None, chunk=None, audit_fail=False):
        self.versions, self.bodies, self.denied, self.chunk = list(versions), bodies or {}, denied, chunk
        self.audit, self.audit_fail, self.sqls, self.args = [], audit_fail, [], []

    async def fetch(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        assert "FROM documents d" in sql and "{" not in sql  # 문서를 읽는 SQL 은 모두 조각이 채워진 채로
        return self.versions

    async def fetchrow(self, sql, *args):
        self.sqls.append(sql)
        self.args.append(args)
        if "is_discoverable" in sql:
            return self.denied
        version_id = args[-2]  # 값은 where 의 값들, doc_id, version_id, chunk_id 순
        return self.bodies.get(version_id) and {**self.bodies[version_id], **(self.chunk or {"chunk_id": None, "section_path": None, "content": None})}

    @asynccontextmanager
    async def acquire(self):
        if self.audit_fail:
            raise ConnectionError("감사 DB 없음")
        yield self

    async def execute(self, sql, *args):
        self.audit.append(args)


class Engine:
    def __init__(self, graph=None):
        self.graph, self.calls, self.todays, self.cfg = graph, [], [], Config(database_url="x")

    async def procedure_graph(self, doc_ids, ctx, *, today):
        self.calls.append(doc_ids)
        self.todays.append(today)
        return [GraphItem(doc_ids[0], self.graph)] if self.graph else []


def body_for(*versions, text="본문"):
    return {v["version_id"]: {"raw_markdown": HEAD.format("ALL_EMP") + text} for v in versions}


def view(pool, c=FIN, doc_id="D", version_tag=None, chunk_id=None, engine=None, **kw):
    async def go():
        try:
            return await service.view(engine or Engine(), pool, DEPTS, RULES, doc_id, version_tag, chunk_id, c, "doc-test",
                                      ip="10.0.0.1", today=TODAY, **kw)
        finally:
            await service.drain()  # 감사는 응답 뒤에 써짐: 끝까지 기다린 뒤 줄을 봄
    return asyncio.run(go())


def error(pool, **kw) -> ApiError:
    with pytest.raises(ApiError) as e:
        view(pool, **kw)
    return e.value


# --- 입력 모양 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("chunk_id, expected", [
    ("T-DOC-008::v1.1::c004", ("T-DOC-008", "v1.1")), ("D::v1.0::c001", ("D", "v1.0")), ("D::v1.0::c1000", ("D", "v1.0")),
    ("D::2026-Q4::c010", ("D", "2026-Q4"))])
def test_parse_chunk_id(chunk_id, expected):
    assert parse_chunk_id(chunk_id) == expected


@pytest.mark.parametrize("chunk_id", ["D", "D::v1.0", "D::v1.0::c01", "D::v1.0::001", "D::v1.0::c001::x", "::v1.0::c001", "D::::c001",
                                      "D::v1.0::cabc", "D:v1.0:c001", " D::v1.0::c001", "D::v1.0::c001\n", "D::v1.0::c００１",
                                      "A:B::v1.0::c001", "D::" + "v" * 33 + "::c001", "D" * 65 + "::v1.0::c001"])
def test_bad_chunk_id_is_422_before_any_db_read(chunk_id):
    pool = Pool()
    e = error(pool, chunk_id=chunk_id)
    assert (e.http_status, e.code) == (422, "VALIDATION_FAILED") and pool.sqls == []


@pytest.mark.parametrize("chunk_id", ["A:B::v1.0::c001", "D::v:1::c001", "D:::v1.0::c001", "D::v1.0:::c001"])
def test_colon_inside_doc_id_or_version_tag_is_ambiguous_and_rejected(chunk_id):
    """`::` 로 나누는 ID 라 doc_id·version_tag 안의 콜론은 어디서 끊을지 모호함 → 모양 오류."""
    with pytest.raises(ApiError) as e:
        parse_chunk_id(chunk_id)
    assert (e.value.http_status, e.value.code) == (422, "VALIDATION_FAILED")
    pool = Pool()
    assert error(pool, doc_id=chunk_id.split("::")[0], chunk_id=chunk_id).http_status == 422 and pool.sqls == []


def test_chunk_id_must_match_path_doc_and_version_tag_separately():
    pool = Pool()
    e = error(pool, doc_id="D", chunk_id="OTHER::v1.0::c001")  # 다른 문서의 조각
    assert (e.http_status, e.code) == (422, "VALIDATION_FAILED") and "doc_id" in e.message
    e = error(pool, doc_id="D", version_tag="v2.0", chunk_id="D::v1.0::c001")  # 다른 판의 조각
    assert (e.http_status, e.code) == (422, "VALIDATION_FAILED") and "version_tag" in e.message
    assert pool.sqls == []  # 둘 다 DB 를 읽기 전에


# --- 머리말 ------------------------------------------------------------------------------------------------

def test_split_header():
    raw = "---\ndoc_id: D\nallowed_depts: [HR, FIN]\neffective_from: 2026-12-01\nsource_url: null\n---\n# 제목\n\n본문\n"
    meta, body = split_header(raw)
    assert meta["allowed_depts"] == ["HR", "FIN"] and meta["source_url"] is None and body == "# 제목\n\n본문\n"  # 본문은 그대로
    assert split_header(raw.replace("\n", "\r\n"))[0]["allowed_depts"] == ["HR", "FIN"]
    assert split_header("# 머리말 없음\n본문") == ({}, "# 머리말 없음\n본문")
    assert split_header("-") == ({}, "-")
    assert split_header("---\nallowed_depts: [HR]\n# 닫히지 않음") == ({}, "---\nallowed_depts: [HR]\n# 닫히지 않음")
    assert split_header("---\na: [\n---\n본문") == ({}, "본문")  # YAML 오류: 머리말은 못 읽어도 뗌
    assert split_header("---\n- a\n- b\n---\n본문") == ({}, "본문")  # 맵이 아님
    assert split_header("본문\n---\nallowed_depts: [HR]\n---\n") == ({}, "본문\n---\nallowed_depts: [HR]\n---\n")  # 맨 앞이 아니면 머리말 아님
    assert split_header(HEAD.format("ALL_EMP") + "---\nx: 1\n---\n")[1] == "# 제목\n---\nx: 1\n---\n"  # 첫 머리말만 뗌


@pytest.mark.parametrize("head, depts, allowed", [
    (HEAD.format("ALL_EMP"), ["PUBLIC", "ALL_EMP", "FIN"], True),
    (HEAD.format("HR"), ["PUBLIC", "ALL_EMP", "FIN"], False),
    (HEAD.format("HR, FIN"), ["PUBLIC", "ALL_EMP", "FIN"], True),
    (HEAD.format("PUBLIC"), ["PUBLIC"], True),
    (HEAD.format("ALL_EMP"), ["PUBLIC"], False),
    (HEAD.format("HR"), None, True),                                  # ADMIN 은 머리말을 보지 않음
    ("-", None, True),
    ("-", ["PUBLIC", "ALL_EMP"], False),                              # 머리말 없음 → 닫힘
    ("", ["PUBLIC"], False),
    ("---\ndoc_id: D\n---\n", ["PUBLIC", "ALL_EMP"], False),          # allowed_depts 없음
    ("---\nallowed_depts: ALL_EMP\n---\n", ["ALL_EMP"], False),       # 목록이 아님
    ("---\nallowed_depts: []\n---\n", ["ALL_EMP"], False),
    ("---\nallowed_depts: [ALL_EMP, 3]\n---\n", ["ALL_EMP"], False),  # 문자열이 아닌 값 섞임
    ("---\nallowed_depts: [ALL_EMP]\n", ["ALL_EMP"], False),          # 머리말이 닫히지 않음(4,096자에 잘린 경우도)
    ("---\nallowed_depts: [\n---\n", ["ALL_EMP"], False),             # YAML 오류
])
def test_header_allows(head, depts, allowed):
    assert header_allows(head, depts) is allowed


# --- 열기: 현행·이전 버전·조항 위치·배지·흐름도 ------------------------------------------------------------------

def test_current_view_shape():
    cur, old = version("v2.0", True, eff=date(2026, 6, 1)), version("v1.0", allowed="HR", eff=date(2026, 1, 1), eff_to=date(2026, 5, 31))
    pool = Pool([cur, old], body_for(cur, old, text="현행 본문"))
    r = view(pool)
    assert (r.doc_id, r.title, r.version_tag, r.is_current, r.version_badge) == ("D", "문서 제목", "v2.0", True, None)
    assert r.dept_name == "재무·회계·세무" and r.body == "# 제목\n현행 본문"
    assert r.location is None and r.workflow is None and r.trace_id == "doc-test"
    assert [v.version_tag for v in r.versions] == ["v2.0"]  # FIN 은 HR 전용 옛 판을 못 엶: 선택기에도 없음
    assert "v1.0" not in r.model_dump_json()


def test_current_view_badge_unverified_scheduled_wins_and_previous_stays_previous():
    old_head = "---\nchapter_id: {}\nallowed_depts: [ALL_EMP]\n---\n# 제목\n"
    stale = {"current_created_at": datetime(2025, 11, 1)}  # 게시한 지 6개월이 넘음
    cur = {**version("v2.0", True), **stale}
    assert view(Pool([cur], body_for(cur))).version_badge == "unverified"
    cur = {**version("v2.0", True), **stale, "last_verified_at": datetime(2026, 11, 1)}  # 최근 확인
    assert view(Pool([cur], body_for(cur))).version_badge is None
    cur = {**version("v2.0", True, eff=date(2026, 12, 1)), **stale}  # 시행 예정이 앞섬
    assert view(Pool([cur], body_for(cur))).version_badge == "scheduled"
    cur = {**version("v2.0", True, head=old_head.format("ch01")), "current_created_at": datetime(2025, 12, 1)}  # 긴 주기(11개월 전)
    assert view(Pool([cur], body_for(cur))).version_badge is None
    cur = {**cur, "current_created_at": datetime(2025, 11, 1)}  # 13개월 전
    assert view(Pool([cur], body_for(cur))).version_badge == "unverified"
    cur, old = {**version("v2.0", True), **stale}, version("v1.0", eff_to=date(2026, 5, 31))
    assert view(Pool([cur, old], body_for(cur, old)), version_tag="v1.0").version_badge == "previous"  # 이전 판은 그대로


def test_one_today_goes_to_every_sql_and_the_graph_call():
    """현행 조건·배지가 같은 "오늘"을 씀: 판 목록·본문 SQL 의 값 자리와 흐름도 호출에 모두 넘김(경계 계약 4.1)."""
    cur = version("v2.0", True)
    pool, engine = Pool([cur], body_for(cur)), Engine()
    view(pool, engine=engine)
    assert pool.args and all(TODAY in a for a in pool.args) and engine.todays == [TODAY]
    pool = Pool(denied=denied())  # 거부 판정 SQL 도 같은 오늘
    error(pool, c=ctx(dept="LGL"), doc_id="HR-1")
    assert len(pool.args) == 2 and all(TODAY in a for a in pool.args)


def test_older_version_opens_with_badge_and_header_check_per_requester():
    cur, old = version("v2.0", True), version("v1.0", allowed="HR", eff_to=date(2026, 5, 31))
    pool = Pool([cur, old], body_for(cur, old))
    e = error(pool, version_tag="v1.0")  # FIN: 머리말 HR 만
    assert (e.http_status, e.code) == (404, "NOT_FOUND")
    assert [a[0] for a in pool.audit] == ["ACL_DENIED"]  # 있는 판을 못 엶 → 감사
    assert pool.audit[0][1:] == (UID, None, f'{{"doc_id": "D", "version_tag": "v1.0", "trace_id": "doc-test"}}', "10.0.0.1")
    r = view(Pool([cur, old], body_for(cur, old)), c=ctx(dept="HR"), version_tag="v1.0")
    assert (r.is_current, r.version_badge, r.workflow) == (False, "previous", None)
    assert [v.version_tag for v in r.versions] == ["v2.0", "v1.0"] and r.effective_to == date(2026, 5, 31)
    admin = view(Pool([cur, old], body_for(cur, old)), c=ADMIN, version_tag="v1.0")  # ADMIN 은 머리말을 보지 않음
    assert admin.version_tag == "v1.0"


def test_scheduled_badge_only_for_current_with_future_effective_date():
    cur = version("v2.0", True, eff=date(2026, 12, 1))
    r = view(Pool([cur], body_for(cur)))
    assert r.version_badge == "scheduled"
    cur = version("v2.0", True, eff=TODAY)  # 오늘 시행은 예정 아님
    assert view(Pool([cur], body_for(cur))).version_badge is None


def test_previous_badge_even_if_old_version_has_future_date():
    cur, old = version("v2.0", True), version("v1.0", eff=date(2099, 1, 1))
    assert view(Pool([cur, old], body_for(cur, old)), version_tag="v1.0").version_badge == "previous"


def test_unknown_or_unlisted_version_is_404_without_audit():
    cur = version("v2.0", True)
    pool = Pool([cur], body_for(cur))
    for tag in ("v9.9", "v2.0 ", "V2.0"):
        assert error(pool, version_tag=tag).code == "NOT_FOUND"
    assert error(pool, version_tag="v9.9", chunk_id="D::v9.9::c001").code == "NOT_FOUND"
    assert pool.audit == []


def test_chunk_id_picks_version_and_reports_location():
    cur, old = version("v2.0", True), version("v1.0")
    chunk = {"chunk_id": "D::v1.0::c002", "section_path": "제1장 > 제2조", "content": "옛 조항 본문"}
    r = view(Pool([cur, old], body_for(cur, old), chunk=chunk), chunk_id="D::v1.0::c002")  # version_tag 없이: chunk_id 의 판
    assert (r.version_tag, r.is_current, r.version_badge) == ("v1.0", False, "previous")
    assert r.location.model_dump() == {"chunk_id": "D::v1.0::c002", "found": True, "section_path": "제1장 > 제2조", "text": "옛 조항 본문"}
    r = view(Pool([cur, old], body_for(cur, old), chunk=chunk), version_tag="v1.0", chunk_id="D::v1.0::c002")  # 같으면 됨
    assert r.version_tag == "v1.0" and r.location.found


def test_pruned_chunk_is_not_found_location_but_version_opens():
    cur, old = version("v2.0", True), version("v1.0")
    r = view(Pool([cur, old], body_for(cur, old, text="정리된 판 본문")), chunk_id="D::v1.0::c001")  # 청크 행 없음
    assert r.location.model_dump() == {"chunk_id": "D::v1.0::c001", "found": False, "section_path": None, "text": None}
    assert r.body.endswith("정리된 판 본문") and r.version_tag == "v1.0"


def test_body_has_no_version_header():
    cur = version("v1.0", True)
    r = view(Pool([cur], {cur["version_id"]: {"raw_markdown": "---\ndoc_id: D\nallowed_depts: [HR]\nreview_reason: null\n---\n# 제목\n본문"}}))
    assert r.body == "# 제목\n본문" and "allowed_depts" not in r.model_dump_json()


def test_workflow_only_with_current_view():
    cur, old = version("v2.0", True), version("v1.0")
    graph = {"workflow_name": "시험 절차", "current_step": 2,
             "prev_steps": [{"step": 1, "doc_id": "P", "title": "앞", "dept_code": "FIN", "is_active": True, "source_doc_id": "D"}],
             "next_steps": [{"step": 3, "doc_id": "N", "title": "뒤", "dept_code": "FIN", "is_active": False, "source_doc_id": "D",
                             "required_form_id": "F"}],
             "conflicts": [{"source_doc_id": "D", "target_doc_id": "X", "target_title": "상대", "clause_pair": "제1조 ↔ 제1조",
                            "priority_rule": "규칙", "target_dept_owner": "HR"}]}
    engine = Engine(graph)
    r = view(Pool([cur, old], body_for(cur, old)), engine=engine)
    assert engine.calls == [["D"]] and r.workflow.workflow_name == "시험 절차" and r.workflow.next_steps[0].required_form_id == "F"
    assert r.workflow.prev_steps[0].required_form_id is None and r.workflow.conflicts[0].target_doc_id == "X"
    assert "target_dept_owner" not in r.model_dump_json()  # 엔진 안쪽 키는 내지 않음(03B 와 같음)
    engine = Engine(graph)
    assert view(Pool([cur, old], body_for(cur, old)), engine=engine, version_tag="v1.0").workflow is None and engine.calls == []
    assert view(Pool([cur], body_for(cur)), engine=Engine(None)).workflow is None  # 그래프 없는 문서


def test_versions_list_order_and_only_openable():
    cur = version("v3.0", True, eff=date(2026, 6, 1))
    rows = [cur, version("v2.0", allowed="ALL_EMP", eff=date(2026, 3, 1)), version("v1.5", allowed="HR", eff=date(2026, 2, 1)),
            version("v1.0", allowed="PUBLIC", eff=date(2025, 1, 1)), version("v0.1", head="-", eff=date(2024, 1, 1))]
    r = view(Pool(rows, body_for(cur)), c=FIN)
    assert [(v.version_tag, v.is_current) for v in r.versions] == [("v3.0", True), ("v2.0", False), ("v1.0", False)]  # HR 전용·머리말 없음은 빠짐
    r = view(Pool(rows, body_for(cur)), c=ADMIN)
    assert [v.version_tag for v in r.versions] == ["v3.0", "v2.0", "v1.5", "v1.0", "v0.1"]


# --- 문서 문에서 걸림: 403 대 404, 감사 -------------------------------------------------------------------------

def denied(discoverable=True):
    return {"is_discoverable": discoverable, "dept_owner": "HR", "dept_name": "인사·노무"}


def test_discoverable_active_account_gets_403_with_cooperation_text_and_audit():
    pool = Pool(denied=denied())
    e = error(pool, doc_id="HR-1")
    assert (e.http_status, e.code, e.message) == (403, "ACL_DENIED", "열람 권한이 없는 문서입니다.")
    assert e.extra == {"dept_owner": "HR", "dept_name": "인사·노무",
                       "text": "본 문서는 인사·노무 열람 제한 규정입니다. 열람이 필요한 경우 사내 그룹웨어에서 인사·노무 앞으로 "
                               "'문서 열람 협조전'을 신청해 주시기 바랍니다."}  # 약속서 00 4.2 예시와 같은 글
    assert pool.audit == [("ACL_DENIED", UID, None, '{"doc_id": "HR-1", "trace_id": "doc-test"}', "10.0.0.1")]


def test_department_label_of_active_pack_wins_in_403():
    e = error(Pool(denied=denied()), doc_id="HR-1")
    assert e.extra["dept_name"] == "인사·노무"
    e = asyncio.run(_deny_with({"HR": "인사 팀"}))
    assert e.extra["dept_name"] == "인사 팀" and "인사 팀 앞으로" in e.extra["text"]


async def _deny_with(departments):
    with pytest.raises(ApiError) as e:
        await service._deny(Pool(denied=denied()), FIN, departments, RULES, "HR-1", "doc-test", None, TODAY)
    await service.drain()
    return e.value


@pytest.mark.parametrize("who", [ANON, ctx(status="PENDING"), ctx(status="REVISION_REQ"), ctx(status="REJECTED"), ctx("system")])
def test_not_active_requesters_get_404_even_if_discoverable_but_audit_is_kept(who):
    pool = Pool(denied=denied(True))
    e = error(pool, c=who, doc_id="HR-1")
    assert (e.http_status, e.code, e.message, e.extra) == (404, "NOT_FOUND", "문서를 찾을 수 없습니다.", {})
    assert pool.audit == [("ACL_DENIED", who.user_id, None, '{"doc_id": "HR-1", "trace_id": "doc-test"}', "10.0.0.1")]  # 비로그인은 actor None


def test_not_discoverable_is_404_same_body_as_missing_and_audited():
    hidden, missing = Pool(denied=denied(False)), Pool(denied=None)
    a, b = error(hidden, doc_id="HR-1"), error(missing, doc_id="HR-1")
    assert (a.http_status, a.code, a.message, a.extra) == (b.http_status, b.code, b.message, b.extra)  # 존재를 알 수 없음
    assert len(hidden.audit) == 1 and missing.audit == []  # 없는 문서는 거짓 기록이라 안 남김


def test_admin_never_denied_and_no_denied_lookup():
    pool = Pool(denied=denied())
    e = error(pool, c=ADMIN, doc_id="HR-1")
    assert e.code == "NOT_FOUND" and pool.audit == []
    assert all("is_discoverable" not in s for s in pool.sqls)  # ADMIN 은 거부 판정 SQL 을 부르지 않음


def test_audit_failure_does_not_change_response(caplog):
    with caplog.at_level(logging.ERROR, logger="rag_api.audit.service"):
        e = error(Pool(denied=denied(), audit_fail=True), doc_id="HR-1")
        e2 = error(Pool(denied=denied(False), audit_fail=True), doc_id="HR-1")
    assert (e.http_status, e2.http_status) == (403, 404)
    assert [r.getMessage() for r in caplog.records] == ["감사 로그를 쓰지 못함 action=ACL_DENIED trace_id=doc-test ConnectionError"] * 2


def test_audit_detail_has_no_title_or_body():
    pool = Pool(denied=denied())
    error(pool, doc_id="HR-1")
    assert "제목" not in str(pool.audit) and "인사" not in str(pool.audit)


# --- 감사를 응답 경로에서 뗌(응답 시간으로 문서가 있는지 드러나지 않게) ---------------------------------------------------

class SlowAuditPool(Pool):
    """감사 INSERT 가 끝나지 않음(release 되기 전까지)."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release, self.started = None, 0

    async def execute(self, sql, *args):
        self.started += 1
        await self.release.wait()
        await super().execute(sql, *args)


def test_responses_do_not_wait_for_the_audit_write_but_the_row_is_written_eventually():
    async def go(c, kind):
        pool = SlowAuditPool(denied=denied(True), versions=[] if kind == "doc" else [version("v2.0", True), version("v1.0", allowed="HR")])
        pool.release = asyncio.Event()
        if kind == "doc":  # 있는데 못 보는 문서(403)
            coro = service.view(Engine(), pool, DEPTS, RULES, "HR-1", None, None, c, "doc-test", ip="10.0.0.1", today=TODAY)
        else:              # 이전 판이 머리말에 막힘(404)
            coro = service.view(Engine(), pool, DEPTS, RULES, "D", "v1.0", None, c, "doc-test", ip="10.0.0.1", today=TODAY)
        with pytest.raises(ApiError) as e:
            await asyncio.wait_for(coro, 1)  # 감사가 끝나지 않아도 응답이 옴
        assert pool.audit == []
        await asyncio.sleep(0)
        assert pool.started == 1  # 쓰기는 이미 시작됨
        pool.release.set()
        await service.drain()
        return e.value, pool.audit
    for kind, status in (("doc", 403), ("version", 404)):
        err, rows = asyncio.run(go(FIN, kind))
        assert err.http_status == status and [r[0] for r in rows] == ["ACL_DENIED"], kind


def test_audit_writes_run_at_most_AUDIT_CONCURRENCY_at_a_time():
    async def go():
        pool = SlowAuditPool(denied=denied(True))
        pool.release = asyncio.Event()
        for i in range(service.AUDIT_CONCURRENCY + 5):
            with pytest.raises(ApiError):  # 감사를 기다리게 되돌린 변형에서 멈추지 않고 실패하게 시간 상한
                await asyncio.wait_for(service.view(Engine(), pool, DEPTS, RULES, f"HR-{i}", None, None, FIN, f"doc-{i}", ip=None,
                                                    today=TODAY), 1)
        for _ in range(5):
            await asyncio.sleep(0)
        assert pool.started == service.AUDIT_CONCURRENCY  # 나머지는 기다림
        pool.release.set()
        await service.drain()
        return len(pool.audit)
    assert asyncio.run(go()) == service.AUDIT_CONCURRENCY + 5  # 그래도 모두 써짐


def test_audit_task_references_are_released_when_done():
    async def go():
        with pytest.raises(ApiError):
            await service.view(Engine(), Pool(denied=denied(True)), DEPTS, RULES, "HR-1", None, None, FIN, "doc-t", ip=None, today=TODAY)
        await service.drain()
        await asyncio.sleep(0)
        return len(service._pending)
    assert asyncio.run(go()) == 0


# --- 앱 종료 때 남은 감사 줄 ----------------------------------------------------------------------------------------

def lifespan_events(monkeypatch, drain):
    from test_app import startup_runner

    run = startup_runner(monkeypatch, create_app)
    events = []

    class ClosingPool:
        def __init__(self, kw):
            self.kwargs = kw

        @asynccontextmanager
        async def acquire(self):
            yield None

        async def close(self):
            events.append("close")

    async def create_pool(dsn, **kw):
        return ClosingPool(kw)

    async def recording_drain():
        events.append("drain")
        await drain()
    monkeypatch.setattr(main.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(service, "drain", recording_drain)
    return run, events


def test_lifespan_drains_audit_writes_before_closing_the_pool(monkeypatch):
    async def done():
        pass
    run, events = lifespan_events(monkeypatch, done)
    run()
    assert events == ["drain", "close"]  # 풀을 닫은 뒤에는 남은 감사 줄을 쓸 수 없음


def test_lifespan_does_not_wait_forever_for_audit_writes(monkeypatch):
    async def never():
        await asyncio.sleep(3600)
    run, events = lifespan_events(monkeypatch, never)
    monkeypatch.setattr(main, "AUDIT_DRAIN_S", 0.05)

    def stuck(*_):
        raise TimeoutError("앱 종료가 감사 쓰기를 끝없이 기다림")
    signal.signal(signal.SIGALRM, stuck)
    signal.alarm(5)  # 상한이 없는 변형에서 시험이 멈추지 않고 실패하게
    try:
        run()  # 시간 상한에서 끝나고 풀은 닫음
    finally:
        signal.alarm(0)
    assert events == ["drain", "close"]


# --- 조항 위치 글 -------------------------------------------------------------------------------------------------

BODY = "# 제목\n\n## 제1장\n\n### 제1조 (목적)\n목적 문단.\n\n### 제2조 (범위)\n범위 문단.\n\n### 제3조 (기타)\n기타 문단.\n"


@pytest.mark.parametrize("content, expected", [
    ("목적 문단.", "목적 문단."),                                              # 그대로 있음
    ("목적 문단.\n", "목적 문단."),                                            # 끝 줄바꿈은 뗌
    ("목적 문단.\n\n범위 문단.", "목적 문단.\n\n### 제2조 (범위)\n범위 문단."),   # 합친 조각: 사이 제목 줄이 낌
    ("목적 문단.\n\n범위 문단.\n\n기타 문단.", "목적 문단.\n\n### 제2조 (범위)\n범위 문단.\n\n### 제3조 (기타)\n기타 문단."),
    ("범위 문단.\n\n기타 문단.", "범위 문단.\n\n### 제3조 (기타)\n기타 문단."),
    ("목적 문단.\n\n없는 문단.", "목적 문단.\n\n없는 문단."),                    # 끝 문단을 못 찾으면 조각 그대로
    ("없는 문단.\n\n범위 문단.", "없는 문단.\n\n범위 문단."),                    # 첫 문단을 못 찾으면 조각 그대로
    ("없는 글", "없는 글"),
])
def test_locate(content, expected):
    assert service.locate(BODY, content) == expected


@pytest.mark.parametrize("name, body, content", [
    ("끝 문단이 뒤 장에만(앞 조항 글은 가림 전이라 어긋남)",
     "A 문단.\n\n### 제2조\nB 문단 (가림 전).\n\n## 제2장\n\n### 제9조\n긴 다른 조항.\n\n### 제10조\nB 문단.\n", "A 문단.\n\nB 문단."),
    ("첫 문단이 다른 문단의 일부", "머리 A 문단. 꼬리\n\n### 제2조\nB 문단.\n", "A 문단.\n\nB 문단."),
    ("끝 문단이 가운데 문단과 같음", "A.\n\n### 2\n같음.\n\n### 3\nC.\n\n### 4\n같음.\n", "A.\n\n같음.\n\nC.\n\n같음."),
    ("끝 문단이 첫 문단 안의 일부", "A 문단 B.\n\n### 2\n가운데.\n", "A 문단 B.\n\nB."),
])
def test_locate_wrong_span_is_rejected_and_falls_back_to_content(name, body, content):
    """잘라 낸 글에서 `#` 줄을 뺀 것이 조각 글과 다르면(엉뚱한 구간) 조각 본문 그대로: 화면이 형광펜을 엉뚱한 곳에 칠하지 않게."""
    assert service.locate(body, content) == content, name


def test_locate_ambiguous_first_paragraph_falls_back_to_content():
    body = "| 항목 | 값 |\n\n### 제1조\n가\n\n| 항목 | 값 |\n\n### 제2조\n나\n"
    content = "| 항목 | 값 |\n\n나"  # 첫 문단이 두 곳 → 어디인지 정할 수 없음
    assert service.locate(body, content) == content


def test_locate_finds_the_last_paragraph_after_the_first_one():
    body = "공통 문장.\n\n### 제1조\n가 문단.\n\n### 제2조\n공통 문장.\n"  # 마지막 문단과 같은 글이 첫 문단 앞에도 있음
    assert service.locate(body, "가 문단.\n\n공통 문장.") == "가 문단.\n\n### 제2조\n공통 문장."


def test_locate_result_is_always_a_substring_when_found():
    for content in ("목적 문단.\n\n범위 문단.", "범위 문단.\n\n기타 문단.", "목적 문단."):
        assert service.locate(BODY, content) in BODY


def test_location_text_in_response_is_found_in_body():
    cur = version("v1.0", True)
    raw = {"raw_markdown": HEAD.format("ALL_EMP") + BODY}
    chunk = {"chunk_id": "D::v1.0::c001", "section_path": "제1장 > 제1조 (목적) & 제2조 (범위)", "content": "목적 문단.\n\n범위 문단."}
    r = view(Pool([cur], {cur["version_id"]: raw}, chunk=chunk), chunk_id="D::v1.0::c001")
    assert r.location.text == "목적 문단.\n\n### 제2조 (범위)\n범위 문단." and r.location.text in r.body


# --- 본문을 읽는 사이에 판이 사라짐 --------------------------------------------------------------------------------

def test_version_vanished_between_queries_is_404():
    cur = version("v1.0", True)
    e = error(Pool([cur], bodies={}))  # 판 목록에는 있었는데 본문 SQL 이 못 찾음(문서 문·판이 그 사이에 바뀜)
    assert (e.http_status, e.code) == (404, "NOT_FOUND")


# --- HTTP: 입력 검증·헤더 ---------------------------------------------------------------------------------------

@pytest.fixture
def app():
    app = create_app()
    app.state.cfg = Config(database_url="x")
    app.state.active_pack, app.state.departments, app.state.rules = "it_service", DEPTS, RULES
    cur = version("v1.0", True)
    app.state.pool, app.state.engine = Pool([cur], body_for(cur)), Engine()
    return app


def get(app, path):
    return request(app, "GET", path)


def test_http_ok_has_no_store_header_and_doc_trace_prefix(app):
    r = get(app, "/api/v1/documents/D")
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    assert r.json()["trace_id"].startswith("doc-") and r.json()["version_tag"] == "v1.0"


@pytest.mark.parametrize("path", ["/api/v1/documents/" + "D" * 65, "/api/v1/documents/D%00X", "/api/v1/documents/D?version_tag=",
                                  "/api/v1/documents/D?version_tag=" + "v" * 33, "/api/v1/documents/D?version_tag=v1%00",
                                  "/api/v1/documents/D?chunk_id=", "/api/v1/documents/D?chunk_id=" + "c" * 129,
                                  "/api/v1/documents/D?chunk_id=D::v1.0::c001%00", "/api/v1/documents/D?chunk_id=bad",
                                  "/api/v1/documents/D?chunk_id=OTHER::v1.0::c001", "/api/v1/documents/D?version_tag=v2.0&chunk_id=D::v1.0::c001"])
def test_http_422_never_reaches_db(app, path):
    r = get(app, path)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED" and r.json()["error"]["trace_id"].startswith("doc-")
    assert app.state.pool.sqls == []
    assert r.headers["cache-control"] == "no-store"  # 오류 응답은 전부 no-store(main.py)


def test_every_error_response_is_no_store_and_success_keeps_private_no_store(app):
    app.state.pool = Pool(denied=denied(True))
    for path, status in (("/api/v1/documents/HR-1", 404), ("/api/v1/documents/D?chunk_id=bad", 422), ("/api/v1/nowhere", 404)):
        r = get(app, path)
        assert (r.status_code, r.headers["cache-control"]) == (status, "no-store"), path
    assert request(app, "POST", "/api/v1/documents/D").headers["cache-control"] == "no-store"  # 405
    assert request(app, "POST", "/api/v1/qa/answers", json={}).headers["cache-control"] == "no-store"  # 401


def test_http_errors_have_common_shape_and_not_cached_as_documents(app):
    app.state.pool = Pool(denied=denied(True))
    r = get(app, "/api/v1/documents/HR-1")  # 비로그인(본체 기본) → 404
    assert r.status_code == 404 and r.json() == {"error": {"code": "NOT_FOUND", "message": "문서를 찾을 수 없습니다.",
                                                           "trace_id": r.json()["error"]["trace_id"]}}
    assert r.json()["error"]["trace_id"].startswith("doc-")


def test_http_403_error_carries_extra_fields(app):
    from rag_api.auth import context

    async def fin(request: Request):
        return FIN
    app.dependency_overrides[context.get_auth_context] = fin
    app.state.pool = Pool(denied=denied(True))
    r = get(app, "/api/v1/documents/HR-1")
    assert r.status_code == 403
    assert set(r.json()["error"]) == {"code", "message", "trace_id", "dept_owner", "dept_name", "text"}  # 제목은 없음


def test_only_get_is_allowed(app):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert request(app, method, "/api/v1/documents/D").status_code == 405
