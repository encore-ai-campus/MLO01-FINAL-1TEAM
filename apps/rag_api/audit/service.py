"""감사 로그 쓰기(audit.md, auth 계획 3.2). `auth_audit_logs` 에 쓰는 SQL 은 이 파일에만 둡니다. 읽는 쪽(내 가입 신청의 마지막 메시지)은 인증 모듈 몫입니다.

`record` 는 부르는 쪽의 트랜잭션 연결로 씁니다: 감사와 같이 바뀌는 쓰기는 같은 트랜잭션이라 감사를 못 쓰면 변경도 되돌아갑니다.
`try_record` 는 상태를 바꾸지 않는 기록(LOGIN·ACL_DENIED)용입니다: 쓰지 못해도 요청을 진행하고 서버 로그에 코드·trace_id 만 남깁니다.
"""
import json
import logging
from uuid import UUID

log = logging.getLogger(__name__)


async def record(conn, action: str, actor: UUID | None, target: UUID | None, detail: dict, ip: str | None) -> None:
    """한 줄 씀. `detail` 에 비밀번호·토큰·해시를 넣지 않음(부르는 쪽 책임)."""
    await conn.execute(
        "INSERT INTO auth_audit_logs (action, actor_user_id, target_user_id, detail_json, ip_address)"
        " VALUES ($1, $2, $3, $4::jsonb, $5)", action, actor, target, json.dumps(detail, ensure_ascii=False), ip)


async def try_record(pool, trace_id: str, action: str, actor: UUID | None, target: UUID | None, detail: dict,
                     ip: str | None) -> None:
    """상태를 바꾸지 않는 기록. 실패해도 예외를 올리지 않음(로그인·답변을 감사 때문에 막지 않음). 원인 글은 남기지 않음:
    asyncpg 오류 글에는 실패한 행의 값이 들 수 있음."""
    try:
        async with pool.acquire() as conn:
            await record(conn, action, actor, target, detail, ip)
    except Exception as e:  # CancelledError 는 BaseException 이라 그대로 올라감
        log.error("감사 로그를 쓰지 못함 action=%s trace_id=%s %s", action, trace_id, type(e).__name__)

