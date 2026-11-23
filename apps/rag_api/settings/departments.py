"""활성 팩과 부서 표시 이름(settings.md §4, 약속서 00 5절)."""
import json
from typing import NamedTuple


def active_pack(active_packs: str) -> str:
    """`ACTIVE_PACKS`(예: base,it_service)의 업무 팩 하나. base 와 업무 팩 하나가 아니거나 benchmark 가 있으면 시작하지 않음.
    ponytail: pack_config.json 과 맞추기(settings.md §4)는 팩 전환을 만들 때."""
    packs = {p.strip() for p in active_packs.split(",") if p.strip()}
    business = packs - {"base", "benchmark"}
    if "base" not in packs or "benchmark" in packs or len(business) != 1:
        raise ValueError(f"ACTIVE_PACKS 는 base 와 업무 팩 하나여야 합니다(benchmark 제외): {active_packs!r}")
    return business.pop()


class QADepartments(NamedTuple):
    name_parts: dict[str, tuple[str, ...]]  # 부서 코드 → 이름 조각(질의가 지목한 부서 찾기, qa 계획 3.2)
    ojt_questions: dict[str, list[str]]     # 부서 코드 → OJT 추천 질문(근거 부족 응답)


async def load(conn, pack: str) -> tuple[dict[str, str], QADepartments]:
    """한 쿼리로 (부서 코드 → 표시 이름, qa 부서 정보). 표시 이름은 활성 팩 라벨(pack_labels[pack]), 없으면
    default_name(폐지 부서도 넣음). 이름 조각은 활성 팩 라벨과 default_name 을 `·` 로 나눈 것.
    ponytail: 시작 때 한 번. OJT 질문 수정 기능(settings.md §3)을 만들 때 다시 읽기."""
    rows = await conn.fetch("SELECT dept_code, default_name, pack_labels::text AS labels, ojt_questions::text AS ojt"
                            " FROM departments")
    names, parts, ojt = {}, {}, {}
    for r in rows:
        code, label = r["dept_code"], json.loads(r["labels"]).get(pack)
        names[code] = label or r["default_name"]
        parts[code] = tuple(dict.fromkeys(p.strip() for name in (label, r["default_name"]) if name
                                          for p in name.split("·") if p.strip()))
        ojt[code] = json.loads(r["ojt"])
    return names, QADepartments(parts, ojt)


async def listing(pool, names: dict[str, str], active_only: bool) -> list[dict]:
    """부서 목록(03C 2절). 요청마다 읽음(10행). 표시 이름은 `names`(시작 때 읽은 활성 팩 라벨), 없으면 default_name.
    `active_only=false` 는 폐지된 부서도(관리 화면이 기존 계정의 폐지 부서 이름을 보일 때)."""
    rows = await pool.fetch("SELECT dept_code, default_name, layer_group, is_active FROM departments"
                            " WHERE is_active OR NOT $1 ORDER BY dept_code", active_only)
    return [{"dept_code": r["dept_code"], "dept_name": names.get(r["dept_code"], r["default_name"]),
             "layer_group": r["layer_group"], "is_active": r["is_active"]} for r in rows]


async def active_codes(conn, codes: list[str]) -> set[str]:
    """`codes` 중 있고 폐지되지 않은 부서(가입·배정에 고를 수 있는 부서, 03C 2절)."""
    return {r["dept_code"] for r in await conn.fetch(
        "SELECT dept_code FROM departments WHERE is_active AND dept_code = ANY($1::text[])", codes)}
