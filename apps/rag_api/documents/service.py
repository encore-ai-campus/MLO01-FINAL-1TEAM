"""원문 보기 `documents.view`(documents.md §6, 원문보기 구현계획).

현행 원문·이전 버전·`chunk_id` 의 조항 위치·협업 흐름도. 열람 판정은 `knowledge_engine.acl` 의 조각을 쓰고(문서 문),
이전 버전은 그 버전 머리말의 `allowed_depts` 도 통과해야 엽니다(5차 회신 2번). 문서 문 SQL 에는 모두 `{where}` 가 있습니다.
"""
import asyncio
import re
import weakref
from datetime import date, datetime
from typing import NoReturn

from rag_api.audit import service as audit
from rag_api.auth.context import AuthContext
from rag_api.contracts.common import KST, ApiError, version_badge
from rag_api.contracts.documents import DocumentResponse, Location, VersionItem, Workflow
from rag_api.knowledge_engine.acl import acl_predicate, active_packs, denied_predicate, render, visible_set
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.rules.values import Rules
from rag_api.shared.version_header import split_header

AUDIT_CONCURRENCY = 4  # 동시에 쓰는 감사 줄 상한(DB 연결을 다 쓰지 않게)
HEAD_CHARS = 4096  # 판 목록에서 판마다 가져오는 본문 앞부분(버전 머리말). 이보다 긴 머리말은 읽을 수 없는 것으로 봄(닫힘)

# 현행 문서의 판 목록(검수 대기본 제외). `{where}` 는 acl_predicate(문서 문: 현행 + 활성 팩 + 열람 집합)
VERSIONS_SQL = """
SELECT d.title, d.doc_type, d.source_tier, d.dept_owner,
       lv.version_id, lv.version_tag, lv.effective_from, lv.effective_to,
       lv.version_id = d.current_version_id AS is_current, left(lv.raw_markdown, {head}) AS head
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
                 JOIN document_versions lv ON lv.doc_id = d.doc_id AND lv.version_status IN ('active', 'archived')
WHERE {where} AND d.doc_id = {doc_id}
ORDER BY is_current DESC, lv.effective_from DESC, lv.version_tag DESC
"""

# 고른 판의 원문과(있으면) 그 판의 조각. 문서 문을 다시 겸
BODY_SQL = """
SELECT lv.raw_markdown, c.chunk_id, c.section_path, c.content
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
                 JOIN document_versions lv ON lv.doc_id = d.doc_id AND lv.version_id = {version_id}
                 LEFT JOIN chunks c ON c.version_id = lv.version_id AND c.chunk_id = {chunk_id}
WHERE {where} AND d.doc_id = {doc_id}
"""

# 거부 판정: 있는데 볼 수 없는 문서의 존재 공개 여부·소관 부서만. 본문·청크는 읽지 않음(존재 공개 안내와 같은 예외)
DENIED_SQL = """
SELECT d.is_discoverable, d.dept_owner, dep.default_name AS dept_name
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
                 JOIN departments dep ON dep.dept_code = d.dept_owner
WHERE {denied} AND d.doc_id = {doc_id}
"""

CHUNK_ID = re.compile(r"([^:]{1,64})::([^:]{1,32})::c[0-9]{3,}")  # {doc_id}::{version_tag}::cNNN(약속서 00 5절)


_pending: set[asyncio.Task] = set()  # 감사 쓰기 작업의 참조(끝나면 빠짐). 작업이 도중에 치워지지 않게
_slots: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # 이벤트 루프마다 세마포어(시험은 루프를 여러 번 만듦)


def audit_later(pool, trace_id: str, actor, detail: dict, ip: str | None) -> None:
    """감사 ACL_DENIED 를 응답 경로 밖에서 씀. 있는데 못 보는 문서만 감사 INSERT 를 기다리면 응답 시간으로 문서가 있는지 드러남(검토).
    예외로 나가는 403·404 에도 걸려야 해서 BackgroundTasks 가 아니라 asyncio 작업. 쓰지 못해도 응답은 그대로(try_record).
    앱이 끝날 때는 lifespan 이 풀을 닫기 전에 drain 으로 남은 줄을 기다림(짧은 시간 상한).
    ponytail: 쌓이는 작업 수에 상한이 없고(동시 쓰기만 AUDIT_CONCURRENCY), 앞 요청이 감사를 띄웠는지가 다음 요청 시간에 조금
    (+0.04ms) 남음. 호출 한도(429)를 만들 때 같이 볼 것."""
    loop = asyncio.get_running_loop()

    async def write() -> None:
        if loop not in _slots:
            _slots[loop] = asyncio.Semaphore(AUDIT_CONCURRENCY)
        async with _slots[loop]:
            await audit.try_record(pool, trace_id, "ACL_DENIED", actor, None, detail, ip)
    task = loop.create_task(write())
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain() -> None:
    """띄운 감사 쓰기를 모두 기다림(시험이 감사 줄을 읽기 전에)."""
    while running := [t for t in _pending if not t.done()]:
        await asyncio.gather(*running)


def locate(body: str, content: str) -> str:
    """조각 본문을 `body` 안에서 그대로 찾히는 글로. 합친 조각은 정제 스크립트가 `### 제N조` 줄을 버리고 문단을 `\\n\\n` 로 이어
    본문과 글자가 다름: 첫 문단 시작부터 마지막 문단 끝까지를 `body` 에서 잘라 냄. 잘라 낸 글에서 `#` 줄을 뺀 것이 조각 글과 같을 때만
    그 글을 씀(엉뚱한 구간을 거르는 검사). 못 정하면(첫 문단이 여러 곳·못 찾음·검사 실패) 조각 본문 그대로.
    ponytail: 마지막 문단은 첫 문단 뒤 처음 나온 곳이라 가운데 문단과 같은 글이면 검사에 걸려 조각 그대로로 물러남."""
    text = content.rstrip("\n")
    if text in body:
        return text
    paras = [p for p in text.split("\n\n") if p]
    if len(paras) < 2 or body.count(paras[0]) != 1:
        return content
    start = body.index(paras[0])
    end = body.find(paras[-1], start + len(paras[0]))
    cut = body[start:end + len(paras[-1])] if end >= 0 else None
    return cut if cut is not None and "\n".join(l for l in cut.split("\n") if not l.startswith("#")) == text else content


def not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "문서를 찾을 수 없습니다.")


def parse_chunk_id(chunk_id: str) -> tuple[str, str]:
    """(doc_id, version_tag). 모양이 틀리면 422."""
    m = CHUNK_ID.fullmatch(chunk_id)
    if not m:
        raise ApiError(422, "VALIDATION_FAILED", "chunk_id: {doc_id}::{version_tag}::cNNN 모양이어야 합니다.")
    return m[1], m[2]


def header_allows(head: str, depts: list[str] | None) -> bool:
    """이전 버전의 머리말 `allowed_depts` 가 열람 집합과 겹치는지. None 은 전체(ADMIN). 읽을 수 없으면 닫힘."""
    if depts is None:
        return True
    allowed = split_header(head)[0].get("allowed_depts")
    return isinstance(allowed, list) and all(isinstance(a, str) for a in allowed) and bool(set(allowed) & set(depts))


async def _deny(pool, ctx: AuthContext, departments: dict[str, str], rules: Rules, doc_id: str, trace_id: str,
                ip: str | None) -> NoReturn:
    """문서 문에서 걸림(설계 §2.3). 있는데 볼 수 없는 문서면 감사 ACL_DENIED 한 줄, 존재 공개 ∧ ACTIVE 계정이면 403, 아니면 404.
    없는 문서·현행이 아닌 문서·활성 팩 밖 문서는 감사 없이 404(거짓 기록 금지). 어느 경우든 같은 404 본문."""
    denied = denied_predicate(ctx, active_packs(ctx, True))  # ADMIN 은 None
    row = None
    if denied is not None:
        sql, args = render(DENIED_SQL, denied=denied, doc_id=doc_id)
        row = await pool.fetchrow(sql, *args)
    if row is None:
        raise not_found()
    audit_later(pool, trace_id, ctx.user_id, {"doc_id": doc_id, "trace_id": trace_id}, ip)
    if ctx.principal_type == "user" and ctx.status == "ACTIVE" and row["is_discoverable"]:
        name = departments.get(row["dept_owner"], row["dept_name"])
        raise ApiError(403, "ACL_DENIED", "열람 권한이 없는 문서입니다.",
                       {"dept_owner": row["dept_owner"], "dept_name": name, "text": rules.cooperation_request.format(dept_name=name)})
    raise not_found()


async def view(engine: KnowledgeEngine, pool, departments: dict[str, str], rules: Rules, doc_id: str, version_tag: str | None,
               chunk_id: str | None, ctx: AuthContext, trace_id: str, *, ip: str | None, today: date | None = None
               ) -> DocumentResponse:
    if chunk_id:  # 두 값을 따로 검사: 다른 문서·다른 판의 chunk_id 가 끼어들지 못하게
        chunk_doc, chunk_tag = parse_chunk_id(chunk_id)
        if chunk_doc != doc_id:
            raise ApiError(422, "VALIDATION_FAILED", "chunk_id: 경로의 doc_id 와 다른 문서의 조각입니다.")
        if version_tag is not None and version_tag != chunk_tag:
            raise ApiError(422, "VALIDATION_FAILED", "chunk_id: version_tag 와 다른 판의 조각입니다.")
        version_tag = chunk_tag

    where = acl_predicate(ctx, active_packs(ctx, True))  # 벤치마크 팩 문서도 원문은 엶(계획 2절)
    sql, args = render(VERSIONS_SQL, where=where, doc_id=doc_id, head=HEAD_CHARS)
    rows = await pool.fetch(sql, *args)
    if not rows:
        await _deny(pool, ctx, departments, rules, doc_id, trace_id, ip)

    depts = visible_set(ctx)
    openable = [r for r in rows if r["is_current"] or header_allows(r["head"], depts)]  # 문서 문 + 이전 버전 머리말
    wanted = next((r for r in rows if (r["is_current"] if version_tag is None else r["version_tag"] == version_tag)), None)
    if wanted is None:
        raise not_found()
    if wanted not in openable:  # 있는 판인데 머리말이 막음
        audit_later(pool, trace_id, ctx.user_id, {"doc_id": doc_id, "version_tag": wanted["version_tag"], "trace_id": trace_id}, ip)
        raise not_found()

    sql, args = render(BODY_SQL, where=where, doc_id=doc_id, version_id=wanted["version_id"], chunk_id=chunk_id)
    body_row = await pool.fetchrow(sql, *args)
    if body_row is None:  # 판 목록을 읽은 뒤 바뀜
        raise not_found()

    body = split_header(body_row["raw_markdown"])[1]
    location = None
    if chunk_id:
        found = body_row["chunk_id"] is not None
        location = Location(chunk_id=chunk_id, found=found, section_path=body_row["section_path"],
                            text=locate(body, body_row["content"]) if found else None)

    is_current = wanted["is_current"]
    badge = version_badge(wanted["effective_from"], today or datetime.now(KST).date()) if is_current else "previous"

    workflow = None
    if is_current:  # 흐름도는 문서의 현행 그래프라 현행 판을 볼 때만
        graph = await engine.procedure_graph([doc_id], ctx)
        workflow = Workflow.model_validate(graph[0].meta) if graph else None

    owner: str = wanted["dept_owner"]
    return DocumentResponse(
        doc_id=doc_id, title=wanted["title"], doc_type=wanted["doc_type"], source_tier=wanted["source_tier"],
        dept_owner=owner, dept_name=departments.get(owner, owner),
        version_id=wanted["version_id"], version_tag=wanted["version_tag"], effective_from=wanted["effective_from"],
        effective_to=wanted["effective_to"], is_current=is_current, version_badge=badge,
        body=body, location=location,
        versions=[VersionItem(version_id=r["version_id"], version_tag=r["version_tag"], effective_from=r["effective_from"],
                              effective_to=r["effective_to"], is_current=r["is_current"]) for r in openable],
        workflow=workflow, trace_id=trace_id)
