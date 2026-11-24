"""본체 DB 시험의 요청자 넣기(인증분리 계획 1.3). 본체에는 인증이 없어 요청자 정보는 `context.get_auth_context` 를 바꿔 끼워 넣습니다.

시험은 시드 계정의 이메일을 `X-Test-User` 헤더로 보내고(없으면 비로그인), 바꿔 끼운 함수가 그 계정의 상태·부서·겸직·역할을 DB 행에서
읽어 요청자 정보를 만듭니다. 로그인·토큰은 쓰지 않습니다. `install` 은 앱을 만드는 `main.create_app` 에 이 바꿔 끼움을 더합니다.
"""
from datetime import UTC, datetime

from fastapi import Request

from rag_api import main
from rag_api.auth import context
from rag_api.auth.context import AuthContext


async def requester(request: Request) -> AuthContext:
    state, email = request.app.state, request.headers.get("x-test-user")
    if not email:
        return AuthContext(principal_type="anonymous", active_pack=state.active_pack, snapshot_at=datetime.now(UTC))
    row = await state.pool.fetchrow("SELECT user_id, status, dept_code, extra_depts, role FROM users WHERE email = $1", email)
    return AuthContext(principal_type="user", user_id=row["user_id"], status=row["status"], dept_code=row["dept_code"],
                       extra_depts=list(row["extra_depts"]), role=row["role"], active_pack=state.active_pack,
                       snapshot_at=datetime.now(UTC))


def install(monkeypatch) -> None:
    real = main.create_app

    def create_app():
        app = real()
        app.dependency_overrides[context.get_auth_context] = requester
        return app
    monkeypatch.setattr(main, "create_app", create_app)


def headers(email) -> dict:
    return {"X-Test-User": email} if email else {}
