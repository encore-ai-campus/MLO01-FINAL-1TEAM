"""보안 정적 검사(계획 4.3)."""
import ast
import re
from pathlib import Path

APP = Path(__file__).parents[2] / "apps" / "rag_api"
KE = APP / "knowledge_engine"
SEARCH = APP / "search"  # 검색도 생성 LLM 0건(search.md, 검색 API 계획 3.4)
DOCUMENTS = APP / "documents"  # 원문 보기도 생성 LLM 0건(원문보기 구현계획 6절)
READS_DOCS = re.compile(r"\b(FROM|JOIN)\s+(chunks|documents)\b", re.I)
# 생성 LLM 클라이언트로 쓰일 수 있는 라이브러리·모듈
LLM_MODULES = re.compile(r"^(openai|anthropic|vllm|ollama|litellm|langchain\w*|google\.generativeai)(\.|$)|(^|\.)(llm\w*|qa)(\.|$)")


def trees(*roots):
    for f in sorted(f for root in roots or (APP,) for f in root.rglob("*.py")):
        yield str(f.relative_to(APP)), ast.parse(f.read_text(encoding="utf-8"))


# `{where}`(acl.py 의 열람 조각) 대신 다른 조각을 쓰거나 조각이 없는 SQL. 이름 → 있어야 할 자리(None 은 없음)
EXEMPT = {
    # 존재 공개 안내: 볼 수 "없는" 문서를 찾으므로 acl.py 의 hidden_predicate 를 `{hidden}` 에 넣음.
    # 공개 표시 문서의 ID·제목·소관 부서명만 읽고 본문·청크는 읽지 않음(설계 §3, K3)
    "DISCOVERABLE_SQL": "{hidden}",
    # 검색 통계: 팩·열람과 관계없이 현행 청크 전체의 토큰 빈도(BM25 통계와 같음).
    # 응답에 내지 않고 근거 부족 판정(IDF 덮음)에만 씀(계획 3.7)
    "TERM_STATS_SQL": None,
    # 원문 보기의 거부 판정: 있는데 볼 수 없는 문서(acl.py 의 denied_predicate)를 `{denied}` 에 넣어 존재 공개 여부·소관 부서만 읽음.
    # 본문·청크는 읽지 않음(감사·403/404 를 가르는 데만, 설계 §2.3)
    "DENIED_SQL": "{denied}",
}


def test_sql_reading_chunks_or_documents_has_acl_slot():
    checked, exempted = 0, set()
    for name, tree in trees():  # 앱 전체(knowledge_engine 밖에서 문서를 읽는 SQL 도 잡음)
        assigned = {id(n.value): t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):  # f-string SQL 은 조각 자리를 감출 수 있어 막음
                text = "".join(p.value for p in node.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
                assert not READS_DOCS.search(text), f"{name}:{node.lineno} f-string 으로 chunks·documents 를 읽음"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and READS_DOCS.search(node.value):
                var = assigned.get(id(node))
                if var in EXEMPT:
                    assert EXEMPT[var] is None or EXEMPT[var] in node.value, f"{name}:{node.lineno} {var}"
                    assert var not in ("DISCOVERABLE_SQL", "DENIED_SQL") or not re.search(r"\b(chunks|raw_markdown)\b", node.value), \
                        "존재 공개·거부 판정은 본문을 읽지 않음(설계 §3 K3)"
                    exempted.add(var)
                    continue
                assert "{where}" in node.value, f"{name}:{node.lineno} 열람 조각 자리({{where}})가 없음"
                checked += 1
    assert checked >= 7  # 키워드·벡터·대표 청크·그래프 출처·그래프 항목·원문 판 목록·원문 본문
    assert exempted == set(EXEMPT)  # 예외 목록이 실제 SQL 을 가리킴


def imported(tree) -> list[tuple[int, str]]:
    """(줄, 모듈) 목록. `from a.b import c` 는 a.b 와 a.b.c 둘 다(c 가 모듈일 수 있음)."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""  # 상대 import(from . import x)는 모듈이 없음
            out += [(node.lineno, m) for m in (base, *(f"{base}.{a.name}" for a in node.names))]
    return out


def test_no_generation_llm_import():
    # 답변 LLM 클라이언트(shared.llm)·openai·qa 를 어떤 import 모양으로 써도 잡는지(qa 계획 4.2). qa 는 LLM 을 쓰는 쪽이라 검사 대상 밖
    for src in ("import openai", "from openai import AsyncOpenAI", "from rag_api.shared.llm import LLMClient",
                "from rag_api.shared import llm", "from rag_api import qa", "import rag_api.qa.service", "from . import llm"):
        assert any(LLM_MODULES.search(m) for _, m in imported(ast.parse(src))), src
    for src in ("from rag_api.shared import config", "from rag_api.knowledge_engine.types import RetrievalQuery"):
        assert not any(LLM_MODULES.search(m) for _, m in imported(ast.parse(src))), src
    names = [name for name, _ in trees(KE, SEARCH, DOCUMENTS)]
    assert {"search/service.py", "knowledge_engine/engine.py", "documents/service.py"} <= set(names)
    for name, tree in trees(KE, SEARCH, DOCUMENTS):
        for lineno, m in imported(tree):
            assert not LLM_MODULES.search(m), f"{name}:{lineno} {m}"


def test_body_has_no_account_writes_and_audit_sql_lives_in_audit_module():
    """올라가는 본체에는 `users` 를 쓰는 SQL 이 없고(계정은 인증 모듈 몫), 감사 로그 SQL 은 audit/service.py 에만(인증분리 계획 1.3).
    문자열 SQL 을 AST 로 훑음."""
    users_write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+users\b", re.I)
    audit_sql = re.compile(r"\bauth_audit_logs\b", re.I)
    hits = {"users": set(), "audit": set()}
    for name, tree in trees():
        for node in ast.walk(tree):
            text = ("".join(p.value for p in node.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
                    if isinstance(node, ast.JoinedStr) else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else "")
            if users_write.search(text):
                hits["users"].add(name)
            if audit_sql.search(text) and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", text, re.I):
                hits["audit"].add(name)
    assert hits == {"users": set(), "audit": {"audit/service.py"}}


def test_body_does_not_import_the_local_auth_package():
    """본체는 인증 구현을 모름: 인증 모듈이 본체를 바깥에서 꽂을 뿐이라 방향이 거꾸로 되면 안 됨(인증분리 계획 1.1)."""
    for name, tree in trees():
        for lineno, m in imported(tree):
            assert not m.startswith("rag_api_local_auth"), f"{name}:{lineno} {m}"
