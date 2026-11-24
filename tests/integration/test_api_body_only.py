"""본체만 띄운 앱(인증 모듈 없음, 인증분리 계획 1.1·3절): 모든 요청이 비로그인이라 공개 문서만 보이고 AI 답변은 401.
다른 DB 시험(test_api_search·test_api_qa)은 요청자를 바꿔 끼우지만(requester.py) 여기서는 바꿔 끼우지 않음."""
from test_retrieve import PUBLIC

SEARCH = {"query": "회사 규정 출장비 정산 기준", "limit": 30}


def test_body_only_app_is_anonymous_public_search_and_401_answer(api):
    async def run(client, conn):
        # 어떤 헤더를 보내도(토큰·부서·역할) 요청자는 늘 비로그인
        for headers in ({}, {"Authorization": "Bearer abc.def.ghi"}, {"X-Dept-Code": "HR", "X-Role": "ADMIN", "X-Test-User": "admin@company.com"}):
            r = await client.post("/api/v1/search/documents", json=SEARCH, headers=headers)
            assert r.status_code == 200, headers
            docs = {d["doc_id"] for d in r.json()["documents"]} | {d["doc_id"] for d in r.json()["reference_templates"]}
            assert docs and docs <= PUBLIC, headers
            r = await client.post("/api/v1/qa/answers", json={"question": "출장비 정산 기준"}, headers=headers)
            assert (r.status_code, r.json()["error"]["code"], r.headers["www-authenticate"]) == (401, "UNAUTHENTICATED", "Bearer"), headers
        # 존재 공개 안내도 비로그인에는 가지 않음
        assert not any(n["kind"] == "discoverable" for n in (await client.post("/api/v1/search/documents", json=SEARCH)).json()["notices"])
    api(run)
