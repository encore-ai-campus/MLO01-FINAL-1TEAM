from datetime import datetime

import pytest

from rag_api.auth.context import AuthContext
from rag_api.knowledge_engine.acl import (Sql, acl_predicate, active_packs, denied_predicate, hidden_predicate, render,
                                         visible_predicate, visible_set)

NOW = datetime(2026, 11, 21)


def ctx(principal="user", status=None, dept=None, extra=(), role=None, pack="it_service"):
    return AuthContext(principal_type=principal, status=status, dept_code=dept, extra_depts=list(extra), role=role,
                       active_pack=pack, snapshot_at=NOW)


# 계획 3.3 표의 모든 칸
@pytest.mark.parametrize("c, expected", [
    (ctx("anonymous"), ["PUBLIC"]),
    (ctx(status="PENDING", dept="HR", role="EMPLOYEE"), ["PUBLIC"]),
    (ctx(status="REVISION_REQ", dept="HR"), ["PUBLIC"]),
    (ctx(status="REJECTED", dept="HR"), ["PUBLIC"]),
    (ctx("system"), ["PUBLIC", "ALL_EMP"]),
    (ctx(status="ACTIVE", dept="HR", role="EMPLOYEE"), ["PUBLIC", "ALL_EMP", "HR"]),
    (ctx(status="ACTIVE", dept="FIN", extra=["HR", "FIN"], role="DEPT_MANAGER"), ["PUBLIC", "ALL_EMP", "FIN", "HR"]),
    (ctx(status="ACTIVE", dept="COMMON", role="ADMIN"), None),
    # 표 밖: 승인 전 ADMIN 역할·상태 없는 user 는 좁은 쪽
    (ctx(status="PENDING", role="ADMIN"), ["PUBLIC"]),
    (ctx(), ["PUBLIC"]),
])
def test_visible_set(c, expected):
    assert visible_set(c) == expected


def test_active_packs():
    assert active_packs(ctx(pack="ecommerce"), False) == ["base", "ecommerce"]
    assert active_packs(ctx(pack="ecommerce"), True) == ["base", "ecommerce", "benchmark"]


def test_acl_predicate_shape():
    p = acl_predicate(ctx(status="ACTIVE", dept="HR"), ["base", "it_service"])
    assert p.text == ("d.current_version_id = v.version_id AND v.version_status = 'active'"
                      " AND d.knowledge_pack = ANY($?::text[]) AND d.allowed_depts && $?::text[]")
    assert p.args == (["base", "it_service"], ["PUBLIC", "ALL_EMP", "HR"])
    admin = acl_predicate(ctx(status="ACTIVE", role="ADMIN"), ["base"], d="x", v="y")
    assert admin.text == "x.current_version_id = y.version_id AND y.version_status = 'active' AND x.knowledge_pack = ANY($?::text[])"
    assert admin.args == (["base"],)


def test_render_numbers_fragments_and_named_slots_in_order():
    where = Sql("a = $?", (1,)) + Sql(" AND b = $?", (2,))
    sql, args = render("SELECT 1 WHERE {where} AND q = {q} OR r = {q} LIMIT {k}", where=where, q="x", k=5)
    assert sql == "SELECT 1 WHERE a = $1 AND b = $2 AND q = $3 OR r = $3 LIMIT $4"
    assert args == [1, 2, "x", 5]
    sql, args = render("{hidden} AND {where} AND {ids}", hidden=Sql("h = $?", ("H",)), where=Sql("w = $?", ("W",)), ids=[1])
    assert (sql, args) == ("h = $1 AND w = $2 AND $3", ["H", "W", [1]])
    with pytest.raises(ValueError):
        render("{where}", where=Sql("a = $?"))


def test_render_keeps_other_braces():
    sql, args = render("SELECT '{}'::jsonb, '{a,b}'::text[], '{x}' WHERE {where} AND x = {q}", where=Sql("t = $?", (1,)), q="v")
    assert sql == "SELECT '{}'::jsonb, '{a,b}'::text[], '{x}' WHERE t = $1 AND x = $2"  # 정해진 이름만 바꿈
    assert args == [1, "v"]


def test_visible_predicate_has_no_current_condition():
    p = visible_predicate(ctx(status="ACTIVE", dept="HR"), ["base", "it_service"])
    assert p.text == "d.knowledge_pack = ANY($?::text[]) AND d.allowed_depts && $?::text[]"
    assert p.args == (["base", "it_service"], ["PUBLIC", "ALL_EMP", "HR"])
    assert visible_predicate(ctx(status="ACTIVE", role="ADMIN"), ["base"]).text == "d.knowledge_pack = ANY($?::text[])"


@pytest.mark.parametrize("c", [ctx("anonymous"), ctx(status="PENDING", dept="HR"), ctx(status="REVISION_REQ", dept="HR"),
                               ctx(status="REJECTED", dept="HR"), ctx("system"), ctx(status="ACTIVE", role="ADMIN")])
def test_hidden_predicate_only_for_active_non_admin(c):
    assert hidden_predicate(c, ["base"]) is None


def test_hidden_predicate_shape():
    p = hidden_predicate(ctx(status="ACTIVE", dept="HR", extra=["FIN"]), ["base", "it_service"])
    assert p.text == ("d.current_version_id = v.version_id AND v.version_status = 'active'"
                      " AND d.knowledge_pack = ANY($?::text[]) AND NOT (d.allowed_depts && $?::text[])")
    assert p.args == (["base", "it_service"], ["PUBLIC", "ALL_EMP", "HR", "FIN"])


@pytest.mark.parametrize("c, depts", [
    (ctx("anonymous"), ["PUBLIC"]), (ctx(status="PENDING", dept="HR"), ["PUBLIC"]), (ctx(status="REVISION_REQ", dept="HR"), ["PUBLIC"]),
    (ctx(status="REJECTED", dept="HR"), ["PUBLIC"]), (ctx("system"), ["PUBLIC", "ALL_EMP"]),
    (ctx(status="ACTIVE", dept="HR"), ["PUBLIC", "ALL_EMP", "HR"])])
def test_denied_predicate_covers_every_non_admin_requester(c, depts):
    """원문 보기의 거부 판정은 존재 공개 안내와 달리 비로그인·승인 전·system 도 "볼 수 없는 문서"를 가림. 안내 대상(ACTIVE)만 hidden 이 같은 조각."""
    p = denied_predicate(c, ["base", "it_service"])
    assert p.text == ("d.current_version_id = v.version_id AND v.version_status = 'active'"
                      " AND d.knowledge_pack = ANY($?::text[]) AND NOT (d.allowed_depts && $?::text[])")
    assert p.args == (["base", "it_service"], depts)
    assert (hidden_predicate(c, ["base", "it_service"]) == p) is (c.principal_type == "user" and c.status == "ACTIVE")


def test_denied_predicate_is_none_for_admin_and_honors_aliases():
    assert denied_predicate(ctx(status="ACTIVE", role="ADMIN"), ["base"]) is None
    assert denied_predicate(ctx("anonymous"), ["base"], d="x", v="y").text.startswith("x.current_version_id = y.version_id AND y.version_status")
