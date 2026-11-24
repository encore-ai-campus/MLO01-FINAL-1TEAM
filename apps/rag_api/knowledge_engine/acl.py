"""열람 판정(설계 §2). 문서·청크를 읽는 SQL 은 모두 이 모듈의 조각을 `render` 로 넣습니다.

- `acl_predicate`: 현행 버전 + 열람(검색·원문·그래프 출처 문서 등 기본)
- `visible_predicate`: 현행 조건을 뺀 열람(그래프 항목의 폐기 문서를 is_active=false 로 남길 때)
- `hidden_predicate`: 볼 수 없는 현행 문서(존재 공개 안내)
"""
import re
from dataclasses import dataclass

from rag_api.auth.context import AuthContext


@dataclass(frozen=True)
class Sql:
    """SQL 조각. 값 자리는 `$?` 이고 번호는 `render` 가 매깁니다. 값을 글자로 이어 붙이지 않습니다."""
    text: str
    args: tuple = ()

    def __add__(self, other: "Sql") -> "Sql":
        return Sql(self.text + other.text, self.args + other.args)


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


def acl_predicate(ctx: AuthContext, packs: list[str], d: str = "d", v: str = "v") -> Sql:
    """문서 `d`·현행 버전 `v` 에 거는 조건. 청크는 `c.version_id = v.version_id` 로 붙입니다."""
    return Sql(f"{d}.current_version_id = {v}.version_id AND {v}.version_status = 'active' AND ") \
        + visible_predicate(ctx, packs, d)


def hidden_predicate(ctx: AuthContext, packs: list[str], d: str = "d", v: str = "v") -> Sql | None:
    """존재 공개 안내(설계 §3): 현행·활성 팩인데 요청자가 볼 수 없는 문서.
    안내는 user·ACTIVE·비ADMIN 에게만 하므로 그 밖은 None."""
    depts = visible_set(ctx)
    if not (ctx.principal_type == "user" and ctx.status == "ACTIVE") or depts is None:
        return None
    return Sql(f"{d}.current_version_id = {v}.version_id AND {v}.version_status = 'active'"
               f" AND {d}.knowledge_pack = ANY($?::text[]) AND NOT ({d}.allowed_depts && $?::text[])", (packs, depts))


def render(template: str, **params) -> tuple[str, list]:
    """`template` 의 `{이름}` 자리를 채우고 `$n` 을 앞에서부터 매깁니다.
    `Sql` 값은 조각 그대로(안의 `$?` 에 번호), 그 밖의 값은 값 자리 하나(같은 이름은 같은 번호).
    정해진 이름만 바꾸므로 다른 중괄호(`'{}'::jsonb` 등)는 그대로지만, 따옴표 안이라도 같은 이름이면 바뀝니다."""
    args: list = []
    slots: dict[str, str] = {}
    for name, value in params.items():
        if isinstance(value, Sql):
            parts = value.text.split("$?")
            if len(parts) - 1 != len(value.args):
                raise ValueError("값 자리 수와 값 수가 다릅니다")
            text = parts[0]
            for arg, part in zip(value.args, parts[1:]):
                args.append(arg)
                text += f"${len(args)}{part}"
            slots[name] = text
        else:
            args.append(value)
            slots[name] = f"${len(args)}"
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, slots)) + r")\}")
    return pattern.sub(lambda m: slots[m.group(1)], template), args
