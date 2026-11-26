"""열람 판정(설계 §2). 문서·청크를 읽는 SQL 은 모두 이 모듈의 조각을 `render` 로 넣습니다.

- `acl_predicate`: 현행 버전(`shared.current_version`) + 열람(검색·원문·그래프 출처 문서 등 기본)
- `visible_predicate`: 현행 조건을 뺀 열람(그래프 항목의 폐기 문서를 is_active=false 로 남길 때)
- `denied_predicate`: 볼 수 없는 현행 문서(직접 열기의 거부 판정)
- `hidden_predicate`: 그중 존재 공개 안내 대상(user·ACTIVE·비ADMIN)
"""
from datetime import date

from rag_api.auth.context import AuthContext
from rag_api.shared.current_version import current_predicate
from rag_api.shared.sql import Sql, render  # noqa: F401 (바깥에서 acl 로 import 하는 이름을 그대로 둠)


def visible_set(ctx: AuthContext) -> list[str] | None:
    """열람 집합(계획 3.3). None 은 전체(ADMIN)."""
    if ctx.principal_type == "system":
        return ["PUBLIC", "ALL_EMP"]
    if ctx.principal_type == "user" and ctx.status == "ACTIVE":
        if ctx.role == "ADMIN":
            return None
        depts = [d for d in (ctx.dept_code, *ctx.extra_depts) if d]
        return list(dict.fromkeys(["PUBLIC", "ALL_EMP", *depts]))
    return ["PUBLIC"]  # 비로그인, 승인 전(PENDING·REVISION_REQ·REJECTED)


def active_packs(ctx: AuthContext, include_benchmark: bool) -> list[str]:
    return ["base", ctx.active_pack, *(["benchmark"] if include_benchmark else [])]


def visible_predicate(ctx: AuthContext, packs: list[str], d: str = "d") -> Sql:
    """현행 조건을 뺀 열람 조건(활성 팩·열람 집합). 폐기 문서도 "볼 수 있는지"만 볼 때(그래프 항목, 설계 §5)."""
    sql = Sql(f"{d}.knowledge_pack = ANY($?::text[])", (packs,))
    depts = visible_set(ctx)
    if depts is not None:
        sql += Sql(f" AND {d}.allowed_depts && $?::text[]", (depts,))
    return sql


def acl_predicate(ctx: AuthContext, packs: list[str], today: date, d: str = "d", v: str = "v") -> Sql:
    """문서 `d`·현행 버전 `v`(종료일이 `today` 이전이면 현행 아님, 4.1) 에 거는 조건. 청크는 `c.version_id = v.version_id` 로 붙입니다."""
    return current_predicate(today, d, v) + Sql(" AND ") + visible_predicate(ctx, packs, d)


def denied_predicate(ctx: AuthContext, packs: list[str], today: date, d: str = "d", v: str = "v") -> Sql | None:
    """현행·활성 팩인데 요청자가 볼 수 없는 문서(직접 열기의 거부 판정, 설계 §2.3). ADMIN 은 볼 수 없는 문서가 없어 None."""
    depts = visible_set(ctx)
    if depts is None:
        return None
    return current_predicate(today, d, v) + Sql(
        f" AND {d}.knowledge_pack = ANY($?::text[]) AND NOT ({d}.allowed_depts && $?::text[])", (packs, depts))


def hidden_predicate(ctx: AuthContext, packs: list[str], today: date, d: str = "d", v: str = "v") -> Sql | None:
    """존재 공개 안내(설계 §3): 볼 수 없는 문서. 안내는 user·ACTIVE·비ADMIN 에게만 하므로 그 밖은 None."""
    if not (ctx.principal_type == "user" and ctx.status == "ACTIVE"):
        return None
    return denied_predicate(ctx, packs, today, d, v)
