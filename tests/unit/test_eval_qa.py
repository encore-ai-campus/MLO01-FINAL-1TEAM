"""AI 답변 평가 스크립트의 판정·집계(qa 계획 4.3 3단계). DB·LLM 없이 작업 파일 줄 모양으로."""
import asyncio
import inspect as pyinspect
from types import SimpleNamespace as NS

import pytest
import yaml
from eval_qa import REPO, ask, code_files, inspect, judge, outside_repo, overlap, summarize
from eval_retrieval import load_meta, visible

from rag_api.shared.tokenize import load_dictionary, tokenize_ko


@pytest.fixture(autouse=True, scope="module")
def dictionary():
    load_dictionary([], "v1.0")


def row(id, behavior="answer_with_citation", result="answered", kind="main", **kw):
    base = {"key": f"{kind}:{id}", "kind": kind, "id": id, "behavior": behavior, "result": result, "latency_s": 1.0,
            "llm": {"answerable": True, "citations": [[1]]}, "benchmark": False, "r3_insufficient": False, "top5": [],
            "leaks": [], "non_current": [], "correct": True}
    return {**base, **kw}


def test_judge_table():
    a = {"result": "answered", "evidence_docs": ["A", "B"], "cards": [("A", "B")], "non_current": []}
    assert judge("answer_with_citation", a, ["B", "Z"]) and not judge("answer_with_citation", a, ["Z"])
    assert judge("answer_with_step_graph", a, ["A", "B", "C"]) and not judge("answer_with_step_graph", a, ["A", "C"])
    assert judge("cite_both_and_prioritize_dept_latest", a, ["A", "B"])
    assert not judge("cite_both_and_prioritize_dept_latest", {**a, "cards": []}, ["A", "B"])  # 카드가 있어야
    assert not judge("active_version_only", {**a, "non_current": ["A::v1::c1"]}, ["A"])
    no = {"result": "insufficient_evidence", "notices": ["discoverable"], "discoverable_docs": ["HR7"], "hidden_cited": []}
    assert judge("insufficient_evidence", no, [])
    # deny_and_guide(7차 회신 2.3절): 응답 종류와 관계없이 숨은 문서 근거·인용 0 + 기대 문서 안내
    assert judge("deny_and_guide_cooperation", no, ["HR7"])                                        # 근거 부족 + 안내
    assert judge("deny_and_guide_cooperation", {**a, "discoverable_docs": ["HR7"], "hidden_cited": []}, ["HR7"])  # 답변 + 안내
    assert not judge("deny_and_guide_cooperation", {**no, "discoverable_docs": []}, ["HR7"])        # 안내 없음
    assert not judge("deny_and_guide_cooperation", {**no, "discoverable_docs": ["HR8"]}, ["HR7"])   # 다른 문서 안내
    assert not judge("deny_and_guide_cooperation", {**a, "discoverable_docs": ["HR7"], "hidden_cited": ["HR7"]}, ["HR7"])  # 숨은 문서 인용
    assert judge("search_no_llm", a, []) is None


def test_summarize_must_table_and_follow_up():
    sent = lambda i, ov, na=False: {"index": i, "text": "t", "citations": [], "overlap": ov, "no_answer": na}
    rows = [row("Q1", sentences=[sent(0, 1.0), sent(1, 0.2, True)], evidence_docs=["A"], dropped=1),
            row("Q2", result="insufficient_evidence", behavior="insufficient_evidence", llm=None, r3_insufficient=True),
            row("Q3", benchmark=True, result="insufficient_evidence", llm=None, r3_insufficient=True, correct=None),
            row("Q4", result="fallback_excerpts", llm={"error": "LLM_TIMEOUT"}, correct=False),
            # 특정 건 처리 현황 조회: 찾지 않고 막아 R3 가 아님(1단계 기준에서는 R3 통과) → R3 차이로 세지 않고 따로
            row("Q5", result="insufficient_evidence", behavior="insufficient_evidence", llm=None, case_status=True),
            row("N1", kind="retry", key="retry:N1<Q1", llm={"answerable": False, "raw_sentences": 0},
                result="insufficient_evidence", correct=None),
            row("N1", kind="retry", key="retry:N1<Q4", llm={"answerable": True, "raw_sentences": 2},
                result="insufficient_evidence", correct=None),
            row("N1", kind="retry", key="retry:N1<Q5", llm=None, result="insufficient_evidence", correct=None)]
    s = summarize(rows, {"Q1": False, "Q2": True, "Q3": True, "Q4": False, "Q5": False})
    assert s["ok"] and s["must"] == {"leaks": 0, "outside_sent_evidence": 0, "r3_diff_from_stage1": []}
    assert s["case_status_blocked"] == ["Q5"]
    assert s["table"]["answer_with_citation"]["n"] == 2 and s["table"]["answer_with_citation"]["correct"] == 1
    # benchmark 정답 문항은 맞음이 아니라 R3 막은 수로(기대가 막힘)
    assert s["table"]["answer_with_citation(benchmark)"] == {"n": 1, "r3_blocked": 1, "results": {"insufficient_evidence": 1}}
    assert (s["llm_called"], s["fallback_codes"]) == (2, ["LLM_TIMEOUT"])
    assert s["dropped_sentences"] == {"total": 1, "no_citation": 0, "out_of_range": 0, "blank": 1}
    assert s["overlap"]["low"] == [{"id": "Q1", "sentence": 1, "overlap": 0.2, "correct": True, "no_answer": True}]
    assert s["no_answer_sentences"]["sentences"] == 1
    assert {k: s["follow_up"][k] for k in ("pairs", "passed_r3", "answerable_false", "all_sentences_dropped", "answered")} == {
        "pairs": 3, "passed_r3": 2, "answerable_false": 1, "all_sentences_dropped": 1, "answered": 0}
    # 반드시 맞을 것이 하나라도 어긋나면 ok=false
    assert not summarize(rows, {"Q1": True, "Q2": True, "Q3": True, "Q4": False, "Q5": False})["ok"]  # R3 가 1단계와 다름
    assert not summarize([row("Q1", leaks=["X"])], {"Q1": False})["ok"]
    assert not summarize([row("Q1", outside_sent=1)], {"Q1": False})["ok"]


def test_overlap():
    tokens = sorted(set(tokenize_ko("법인카드 정산 기한")))  # 문장 토큰은 tokenize_ko 와 같은 규칙(청크 lexemes 와 같음)
    assert overlap("법인카드 정산 기한", set(tokens)) == 1.0
    assert overlap("법인카드 정산 기한", set(tokens[:1])) == round(1 / len(tokens), 3)
    assert overlap("법인카드 정산 기한", set()) == 0.0
    assert overlap("!!!", {"a"}) is None  # 토큰이 없음


def chunk(doc, tier, n=1):
    return NS(chunk_id=f"{doc}::v1::c{n}", doc=NS(doc_id=doc, source_tier=tier), lexemes=["정산"])


def answered(*chunk_ids):
    return NS(result="answered", evidence=[NS(chunk_id=c, doc_id=c.split("::")[0]) for c in chunk_ids],
              answer=[NS(text="정산 기한", citations=list(chunk_ids))], conflict_clauses=[], notices=[], header=None)


def test_inspect_checks_against_sent_evidence_not_response():
    """반드시 맞을 것을 응답끼리 비교하지 않음: LLM 에 넘긴 근거(select_evidence)와 검색 상위로 따로 봄(검토 M5)."""
    res = NS(chunks=[chunk("S", 2), chunk("P", 1), chunk("H", 1, 2)])  # 상위 2(S·P)를 1순위 먼저 LLM 에. H 는 상위 밖
    case = {"expected_doc_ids": ["S"]}
    tags = {"S": "v1", "P": "v1", "H": "v1"}
    llm = {"answerable": True, "citations": [[1], [], [9]]}
    ok = inspect(case, answered("P::v1::c1"), res, llm, 2, lambda d: d != "X", tags, {})
    assert (ok["sent"], ok["outside_sent"], ok["leaks"]) == (["P::v1::c1", "S::v1::c1"], 0, [])
    assert (ok["dropped"], ok["drop_reasons"]) == (2, {"no_citation": 1, "out_of_range": 1})
    # 근거 목록과 인용을 함께 넘기지 않은 청크(H)로 바꾼 응답: 응답 안에서는 맞아 보여도 넘긴 근거 밖
    m5 = inspect(case, answered("H::v1::c2"), res, llm, 2, lambda d: True, tags, {})
    assert m5["outside_sent"] == 2
    row = {"key": "main:Q", "kind": "main", "id": "Q", "behavior": "answer_with_citation", "result": "answered",
           "latency_s": 1.0, "llm": llm, "benchmark": False, "r3_insufficient": False, "correct": True, **m5}
    assert not summarize([row], {"Q": False})["ok"]
    # 응답에 안 보인 검색 상위 문서도 열람 판정
    assert inspect(case, answered("P::v1::c1"), res, llm, 2, lambda d: d != "S", tags, {})["leaks"] == ["S"]


def test_inspect_records_hidden_cited_and_notice_docs():
    """deny_and_guide 판정 재료: 근거·인용·발췌에 보인 볼 수 없는 문서, 존재 공개 안내 제목 → 문서 ID."""
    res, tags = NS(chunks=[chunk("P", 1), chunk("H", 1)]), {"P": "v1", "H": "v1"}
    hint = NS(kind="discoverable", title="연봉 밴드")
    titles = {"H": "연봉 밴드", "P": "출장비"}
    r = answered("P::v1::c1")
    r.notices = [hint]
    row = inspect({"expected_doc_ids": ["H"]}, r, res, None, 5, lambda d: d != "H", tags, titles)
    assert (row["discoverable_docs"], row["hidden_cited"], row["notices"]) == (["H"], [], ["discoverable"])
    assert row["unmatched_notice_titles"] == []
    r.notices = [hint, NS(kind="discoverable", title="정제본에 없는 제목"), NS(kind="legal_disclaimer", title=None)]
    row = inspect({"expected_doc_ids": ["H"]}, r, res, None, 5, lambda d: d != "H", tags, titles)
    assert (row["discoverable_docs"], row["unmatched_notice_titles"]) == (["H"], ["정제본에 없는 제목"])  # 되찾지 못한 제목은 남김
    assert judge("deny_and_guide_cooperation", {**row, "result": "answered"}, ["H"])  # 답변 + 안내
    row = inspect({"expected_doc_ids": ["H"]}, answered("H::v1::c1"), res, None, 5, lambda d: d != "H", tags, titles)
    assert row["hidden_cited"] == ["H"] and not judge("deny_and_guide_cooperation", {**row, "result": "answered"}, ["H"])
    fb = NS(result="fallback_excerpts", excerpts=[NS(doc_id="H", chunk_id="H::v1::c1")], notices=[hint])
    row = inspect({"expected_doc_ids": ["H"]}, fb, res, None, 5, lambda d: d != "H", tags, titles)
    assert (row["hidden_cited"], row["discoverable_docs"]) == (["H"], ["H"])  # 대체 응답 발췌도 봄


def test_work_file_must_be_outside_repo(tmp_path):
    assert outside_repo(tmp_path / "w.jsonl") == tmp_path / "w.jsonl"
    with pytest.raises(SystemExit, match="저장소 안"):
        outside_repo(REPO / "eval" / "results" / "w.jsonl")
    swapped = REPO.parent / REPO.name.swapcase()  # 대소문자만 바꾼 저장소 경로(macOS 기본 디스크는 같은 디렉터리)
    if not swapped.exists():
        pytest.skip("대소문자를 가리는 디스크")
    with pytest.raises(SystemExit, match="저장소 안"):
        outside_repo(swapped / "eval" / "w.jsonl")


def test_code_hash_covers_app_and_eval_scripts():
    files = code_files()
    assert REPO / "apps" / "rag_api" / "qa" / "service.py" in files
    assert {f.name for f in files} >= {"eval_qa.py", "eval_retrieval.py"}


def test_visible_and_load_meta(tmp_path):
    """평가가 구현과 따로 적은 열람·팩 판정(eval_retrieval·eval_qa 같이 씀)."""
    docs = [{"doc_id": "P", "version_status": "active", "allowed_depts": ["PUBLIC"], "knowledge_pack": "base"},
            {"doc_id": "E", "version_status": "archived", "allowed_depts": ["HR"], "knowledge_pack": "base"},
            {"doc_id": "E", "version_status": "active", "allowed_depts": ["ALL_EMP"], "knowledge_pack": "base"},
            {"doc_id": "H", "version_status": "active", "allowed_depts": ["HR"], "knowledge_pack": "base"},
            {"doc_id": "C", "version_status": "active", "allowed_depts": ["ALL_EMP"], "knowledge_pack": "ecommerce"},
            {"doc_id": "B", "version_status": "active", "allowed_depts": ["ALL_EMP"], "knowledge_pack": "benchmark"}]
    (tmp_path / "documents.yaml").write_text(yaml.safe_dump(docs, allow_unicode=True), encoding="utf-8")
    meta = load_meta(tmp_path)
    assert meta["E"]["allowed_depts"] == ["ALL_EMP"]  # 현행 판이 먼저
    seen = lambda case: [d for d in meta if visible(meta, case, d)]
    assert seen({"actor_dept": "PUBLIC"}) == ["P"]
    assert seen({"actor_dept": "FIN"}) == ["P", "E"]
    assert seen({"actor_dept": "HR"}) == ["P", "E", "H"]
    assert seen({"actor_dept": "FIN", "pack": "ecommerce"}) == ["P", "E", "C"]  # 벤치마크 팩은 늘 밖


def test_eval_calls_qa_answer_with_its_real_signature_and_no_audit_pool(monkeypatch):
    """평가가 qa.answer 를 부르는 모양(인수 수·이름)이 맞는지, 그리고 pool 을 넘기지 않는지(개발 DB 에 감사 로그를 쓰지 않음, 검토 K2).
    answer 에 필수 인수가 더해지면 평가가 모든 문항에서 TypeError 로 멈추는데 main 은 시험이 부르지 않아 잡히지 않았음."""
    from rag_api.qa import service
    real = pyinspect.signature(service.answer)
    seen = []

    async def spy(*args, **kw):
        bound = real.bind(*args, **kw)  # 안 맞으면 TypeError
        bound.apply_defaults()
        seen.append(bound.arguments)
        return "ok"
    monkeypatch.setattr(service, "answer", spy)
    case, prev = {"id": "Q1", "query": "정산 기한"}, {"id": "Q0", "query": "법인카드"}
    assert asyncio.run(ask("engine", "llm", "rules", {}, "qa_depts", case, prev, "ctx")) == "ok"
    args = seen[0]
    assert (args["req"].question, args["req"].previous_query, args["trace_id"]) == ("정산 기한", "법인카드", "qa-eval-Q1")
    assert args["pool"] is None and args["ip"] is None
