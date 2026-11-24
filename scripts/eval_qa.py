"""Golden QA v2 AI 답변 평가(qa 구현 계획 4.3 3단계). 개발 DB(rag_dev)와 LLM 서버로 `qa.answer` 를 끝까지 돌립니다.

- 문항: mode 가 qa 인 118문항. 질문자는 eval_retrieval.py 와 같음(actor_dept, COMMON 은 EMPLOYEE, 문항의 pack).
- 반드시 맞을 것(하나라도 어긋나면 결과의 ok=false, 끝 코드 1). 응답만 보지 않고 평가가 따로 잡은 검색 결과로 봄
  - 열람 밖 문서 0건: 응답에 보인 문서(근거·발췌·카드 상대)와 검색 상위 N 문서·LLM 에 넘긴 근거 문서를
    정제본 documents.yaml 로 구현과 따로 판정
  - LLM 에 넘긴 근거(평가가 검색 결과에 select_evidence 를 걸어 다시 구한 chunk_id) 밖을 가리키는 인용·응답 근거 0건
  - 근거 부족 판정(R3, LLM 앞)이 기준 파일(1단계 결과)과 문항 ID 단위로 같음
- 수로 보고(개발 모델이라 통과 기준 아님): expected_behavior 별 맞은 수(정답이 benchmark 팩인 문항은 R3 막음으로 따로),
  answerable=false·빠진 문장(이유별)·대체 응답 수, 지연, "근거에 답이 없다" 문장과 그 문항의 상위 5 등급, 문장과 인용
  청크의 토큰 겹침, 이어 묻기(처음 막힌 문항 × 같은 부서 답 있는 질문을 previous_query 로 붙인 쌍).
- 문항마다 작업 파일(JSONL)에 한 줄씩 적고, 다시 돌리면 같은 설정(코드·규칙·데이터 해시 포함)으로 적힌 줄은 건너뜀.
  작업 파일에는 질의·답변 글이 들어가 저장소 밖 경로만 받음. 결과 JSON 에는 집계와 문항 ID 단위 값만(질의·본문 없음).

환경 변수: eval_retrieval.py 와 같음 + LLM_BASE_URL·LLM_MODEL 등(shared.config)
사용: PYTHONPATH=apps:scripts python scripts/eval_qa.py <작업 파일(저장소 밖)> [결과(기본 eval/results/qa_eval.json)]
      [근거 부족 기준(기본 eval/results/retrieval_eval.json)]
"""
import asyncio
import hashlib
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from eval_retrieval import actor, load_meta, open_engine, visible

from rag_api.contracts.common import KST
from rag_api.contracts.qa import QARequest
from rag_api.qa import service
from rag_api.rules import values
from rag_api.settings import departments
from rag_api.shared.config import Config
from rag_api.shared.llm import LLMClient, LLMFailed
from rag_api.shared.tokenize import tokenize_ko

REPO = Path(__file__).resolve().parents[1]
NEGATIVE = ("insufficient_evidence", "acl_block")  # 근거 없는 질문·열람 차단 분류(이어 묻기의 앞 문항)
# answered 인데 근거에 답이 없다고 말하는 문장(4.3). ponytail: 문구 몇 가지만. 새 말투가 보이면 더함
NO_ANSWER = re.compile(r"(명시|언급|기재|규정|나와|포함|제시)\s*(되어|돼)?\s*있지\s*않|확인할\s*수\s*없|알\s*수\s*없|"
                       r"(제공된|주어진)\s*(자료|근거|문서|정보)(에는|에서는)")
LOW_OVERLAP = 0.5  # 토큰 겹침이 이보다 낮은 문장을 목록으로


def overlap(text: str, lexemes: set[str]) -> float | None:
    """문장 토큰(tokenize_ko, 중복 없이) 중 인용 청크 lexemes 에 있는 몫. 토큰이 없으면 None(계획 3.5)."""
    tokens = set(tokenize_ko(text))
    return round(len(tokens & lexemes) / len(tokens), 3) if tokens else None


def judge(behavior: str, row: dict, expected: list[str]) -> bool | None:
    """4.3 표의 "맞았다고 셀 조건". 표에 없는 행동은 None."""
    cited = set(row.get("evidence_docs", []))
    answered = row["result"] == "answered"
    if behavior == "answer_with_citation":
        return answered and bool(cited & set(expected))
    if behavior == "insufficient_evidence":
        return row["result"] == "insufficient_evidence"
    if behavior == "deny_and_guide_cooperation":
        # 7차 회신 2.3절(백엔드 결정): 응답 종류(근거 부족·답변·대체)와 관계없이, 볼 수 없는 문서가 근거·인용에 없고
        # 존재 공개 안내에 기대 문서가 있으면 맞음
        return bool(expected) and not row.get("hidden_cited") and set(expected) <= set(row.get("discoverable_docs", []))
    if behavior == "answer_with_step_graph":
        return answered and len(cited & set(expected)) >= 2
    if behavior == "cite_both_and_prioritize_dept_latest":
        return answered and set(expected) <= cited and bool(row.get("cards"))
    if behavior == "active_version_only":
        return answered and not row.get("non_current")
    return None


def drop_reasons(cited: list[list[int]], n: int) -> dict[str, int]:
    """LLM 이 낸 문장별 인용 번호로 인용 검증에서 빠질 이유(qa.service.validate 와 같은 조건, 빈 문장은 글이 없어 못 셈)."""
    return {"no_citation": sum(not c for c in cited), "out_of_range": sum(bool(c) and not all(1 <= x <= n for x in c) for c in cited)}


def inspect(case: dict, r, res, llm: dict | None, n: int, can_see, current_tag: dict[str, str],
            titles: dict[str, str]) -> dict:
    """한 문항의 응답 `r` 과 평가가 따로 잡은 마지막 검색 결과 `res` 로 작업 파일 줄의 판정 값을 만듦(DB·LLM 없음).
    `llm` 은 LLM 출력 기록(answerable·문장별 인용 번호) 또는 실패 코드, `can_see(doc_id)` 는 구현과 따로 적은 열람 판정,
    `titles` 는 문서 ID → 제목(정제본, 존재 공개 안내는 제목만 내므로 문서로 되돌림)."""
    top = res.chunks[:n] if res else []
    sent = [c.chunk_id for c in service.select_evidence(res.chunks, n)] if res else []  # LLM 에 넘긴 근거(번호 순)
    row = {"top5": [(c.doc.doc_id, c.doc.source_tier) for c in top], "sent": sent}
    shown, versions, cited = [], [], []
    if r.result == "answered":
        lex = {c.chunk_id: set(c.lexemes) for c in res.chunks}
        row["evidence_docs"] = list(dict.fromkeys(e.doc_id for e in r.evidence))
        # 근거 목록 밖 인용: 응답 근거와 인용이 모두 LLM 에 넘긴 근거 안인지(응답끼리 비교하지 않음)
        row["outside_sent"] = sum(c not in sent for c in [*(e.chunk_id for e in r.evidence),
                                                          *(c for s in r.answer for c in s.citations)])
        row["cards"] = [(c.source_doc_id, c.target_doc_id) for c in r.conflict_clauses]
        row["header"] = r.header is not None
        row["sentences"] = [{"index": i, "text": s.text, "citations": s.citations, "no_answer": bool(NO_ANSWER.search(s.text)),
                             "overlap": overlap(s.text, set().union(*(lex.get(c, set()) for c in s.citations)))}
                            for i, s in enumerate(r.answer)]
        shown = row["evidence_docs"] + [t for _, t in row["cards"]]
        versions = [e.chunk_id for e in r.evidence]
        cited = row["evidence_docs"] + [c.split("::")[0] for s in r.answer for c in s.citations]
    elif r.result == "fallback_excerpts":
        row["excerpt_docs"] = list(dict.fromkeys(e.doc_id for e in r.excerpts))
        shown = cited = row["excerpt_docs"]
        versions = [e.chunk_id for e in r.excerpts]
    row["notices"] = [x.kind for x in r.notices]
    discoverable = {x.title for x in r.notices if x.kind == "discoverable"}
    row["discoverable_docs"] = sorted(d for d, t in titles.items() if t in discoverable)
    row["unmatched_notice_titles"] = sorted(discoverable - set(titles.values()))  # 정제본 제목으로 되찾지 못한 안내
    row["hidden_cited"] = sorted({d for d in cited if not can_see(d)})  # 근거·인용·발췌에 보인 볼 수 없는 문서
    if llm and "citations" in llm:  # LLM 이 낸 문장 중 빠진 수와 이유
        if not llm["answerable"]:  # 답할 수 없다고 했으면 문장을 보지 않음(빠진 것이 아님)
            row["dropped"], row["drop_reasons"] = 0, {}
        else:
            row["dropped"] = len(llm["citations"]) - (len(r.answer) if r.result == "answered" else 0)
            row["drop_reasons"] = drop_reasons(llm["citations"], len(sent))
    row["leaks"] = sorted({d for d in [*shown, *(c.doc.doc_id for c in top)] if not can_see(d)})  # 넘긴 근거 = 상위 n
    row["non_current"] = [v for v in versions if v.split("::")[1] != current_tag[v.split("::")[0]]]
    return row


def pct(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def summarize(rows: list[dict], ref: dict[str, bool]) -> dict:
    """작업 파일의 줄(문항 `main`, 이어 묻기 `retry`)을 결과 요약으로. `ref` 는 문항 ID → 1단계 근거 부족 판정."""
    main = [r for r in rows if r["kind"] == "main"]
    retry = [r for r in rows if r["kind"] == "retry"]
    leaks = sum(len(r["leaks"]) for r in rows)
    outside = sum(r.get("outside_sent", 0) for r in rows)
    r3_diff = sorted(r["id"] for r in main if r["r3_insufficient"] != ref.get(r["id"]))
    table = {}
    for r in main:
        if r["benchmark"]:  # 정답이 일반 검색 밖이라 R3 가 막는 것이 기대(4.3 표). 맞음으로 세지 않고 막은 수로
            t = table.setdefault("answer_with_citation(benchmark)", {"n": 0, "r3_blocked": 0, "results": {}})
            t["r3_blocked"] += r["r3_insufficient"]
        else:
            t = table.setdefault(r["behavior"], {"n": 0, "correct": 0, "results": {}})
            t["correct"] += bool(r["correct"])
        t["n"] += 1
        t["results"][r["result"]] = t["results"].get(r["result"], 0) + 1
    sentences = [dict(s, id=r["id"], correct=r["correct"]) for r in main for s in r.get("sentences", [])]
    ov = [s["overlap"] for s in sentences if s["overlap"] is not None]
    low = [s for s in sentences if s["overlap"] is not None and s["overlap"] < LOW_OVERLAP]
    llm_rows = [r for r in main if r["llm"]]
    lat = [r["latency_s"] for r in main]
    no_answer = [r for r in main if any(s["no_answer"] for s in r.get("sentences", []))]
    called = [r for r in retry if r["llm"]]
    reasons = {k: sum(r.get("drop_reasons", {}).get(k, 0) for r in main) for k in ("no_citation", "out_of_range")}
    dropped = sum(r.get("dropped") or 0 for r in main)
    return {
        "ok": leaks == 0 and outside == 0 and not r3_diff,
        "must": {"leaks": leaks, "outside_sent_evidence": outside, "r3_diff_from_stage1": r3_diff},
        "questions": len(main), "table": dict(sorted(table.items())),
        "case_status_blocked": sorted(r["id"] for r in main if r.get("case_status")),  # 찾지 않고 막은 특정 건 처리 현황 조회
        "results": {k: sum(r["result"] == k for r in main) for k in ("answered", "insufficient_evidence", "fallback_excerpts")},
        "llm_called": len(llm_rows),
        "answerable_false": sum(r["llm"].get("answerable") is False for r in llm_rows),
        "dropped_sentences": {"total": dropped, **reasons, "blank": dropped - sum(reasons.values())},  # 빈 문장은 글이 없어 나머지로
        "fallback_codes": sorted(r["llm"]["error"] for r in llm_rows if "error" in r["llm"]),
        "latency_s": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "llm_p50": pct([r["latency_s"] for r in llm_rows], 0.5),
                      "llm_p95": pct([r["latency_s"] for r in llm_rows], 0.95)},
        "no_answer_sentences": {"sentences": sum(s["no_answer"] for s in sentences),
                                "questions": [{"id": r["id"], "correct": r["correct"], "top5_tiers": [t for _, t in r["top5"]]}
                                              for r in no_answer]},
        "overlap": {"sentences": len(ov), "min": min(ov, default=None), "p10": pct(ov, 0.1),
                    "median": statistics.median(ov) if ov else None, "low_threshold": LOW_OVERLAP,
                    "low_sentences": {"correct_question": sum(bool(s["correct"]) for s in low),
                                      "wrong_question": sum(s["correct"] is False for s in low),
                                      "questions": len({s["id"] for s in low})},
                    "low": [{"id": s["id"], "sentence": s["index"], "overlap": s["overlap"], "correct": s["correct"],
                             "no_answer": s["no_answer"]} for s in low]},
        "follow_up": {"pairs": len(retry), "passed_r3": len(called),
                      "answerable_false": sum(r["llm"].get("answerable") is False for r in called),
                      "all_sentences_dropped": sum(r["llm"].get("answerable") is True and r["result"] == "insufficient_evidence"
                                                   for r in called),
                      "answered": sum(r["result"] == "answered" for r in called),
                      "fallback": sum(r["result"] == "fallback_excerpts" for r in called),
                      "by_question": {q: {"pairs": sum(r["id"] == q for r in called),
                                          "answered": sum(r["id"] == q and r["result"] == "answered" for r in called)}
                                      for q in sorted({r["id"] for r in called})},
                      "leaks": sum(len(r["leaks"]) for r in retry)},
    }


class Recording:
    """LLM 클라이언트를 감싸 문항마다 LLM 출력(answerable·문장별 인용 번호, 글 없음) 또는 실패 코드를 남김."""
    def __init__(self, llm: LLMClient):
        self.llm, self.calls = llm, []

    async def parse(self, messages, schema):
        try:
            out = await self.llm.parse(messages, schema)
        except LLMFailed as e:
            self.calls.append({"error": e.code})
            raise
        self.calls.append({"answerable": out.answerable, "citations": [s.citations for s in out.sentences]})
        return out


def sha256(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in map(Path.resolve, paths):
        h.update(str(p.relative_to(REPO) if p.is_relative_to(REPO) else p.name).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:16]


def code_files() -> list[Path]:
    """이어 돌리기 판단의 코드 판: 앱 전체와 평가 스크립트(이 파일·eval_retrieval)."""
    return [*sorted((REPO / "apps" / "rag_api").rglob("*.py")), Path(__file__), REPO / "scripts" / "eval_retrieval.py"]


def outside_repo(path: Path) -> Path:
    """작업 파일(질의·답변 글)은 저장소 밖에만. 같은 디렉터리인지는 samefile(대소문자를 가리지 않는 디스크에서도)."""
    if any(p.exists() and p.samefile(REPO) for p in path.resolve().parents):
        raise SystemExit(f"작업 파일 {path} 이 저장소 안입니다. 질의·답변 글이 들어가므로 저장소 밖 경로를 주세요")
    return path


async def ask(engine, llm, rules, names, qa_depts, case, prev, ctx):
    """한 문항을 qa.answer 로. `pool` 을 넘기지 않음: 평가는 개발 DB 에 감사 로그(ACL_DENIED)를 쓰지 않음(auth 계획 검토 K2)."""
    return await service.answer(engine, llm, rules, names, qa_depts,  # type: ignore[arg-type]  parse 가 같은 감싸개
                                QARequest(question=case["query"], previous_query=prev["query"] if prev else None),
                                ctx, f"qa-eval-{case['id']}", pool=None, ip=None)


async def main(work: Path, out: Path, ref_path: Path) -> int:
    cfg = Config.from_env()
    if not cfg.llm_base_url:
        raise SystemExit("LLM_BASE_URL 이 없습니다(평가는 LLM 까지 돌림)")
    data, golden = Path(os.environ["RAG_DATA_DIR"]), Path(os.environ["RAG_GOLDEN_QA"])
    meta = load_meta(data)
    current_tag = {k: d["version_tag"] for k, d in meta.items()}
    titles = {k: d["title"] for k, d in meta.items()}
    assert len(set(titles.values())) == len(titles), "정제본에 같은 제목의 문서가 있어 존재 공개 안내 제목으로 문서를 되찾을 수 없음"
    cases = yaml.safe_load(golden.read_text(encoding="utf-8"))["cases"]
    ref_data = json.loads(ref_path.read_text(encoding="utf-8"))
    ref = {q["id"]: q["insufficient"] for q in ref_data.get("rows") or ref_data["questions"]}

    config = {k: getattr(cfg, k) for k in (
        "llm_model", "llm_temperature", "llm_max_tokens", "llm_reasoning_effort", "llm_timeout_s", "llm_max_concurrency",
        "llm_slot_wait_s", "qa_evidence_chunks", "insufficient_coverage_min", "insufficient_cosine_min",
        "insufficient_coverage_chunks", "embed_model")}
    config |= {"expansion": cfg.ontology_expansion_enabled, "stage1_ref": ref_path.name,
               # 이어 돌리기 판단에 코드·데이터 판도 넣음(바뀌면 멈춤)
               "code_sha": sha256(*code_files()),
               "rules_sha": sha256(Path(cfg.rules_path or values.DEFAULT_PATH)), "golden_sha": sha256(golden),
               "documents_sha": sha256(data / "documents.yaml")}
    done = {}
    if work.exists():
        head, *lines = work.read_text(encoding="utf-8").splitlines()
        if json.loads(head)["config"] != config:
            raise SystemExit(f"작업 파일 {work} 의 설정(코드·데이터 해시 포함)이 지금과 다릅니다. 지우고 다시 돌리세요")
        started = json.loads(head)["started_at"]
        done = {r["key"]: r for r in map(json.loads, lines)}
    else:
        started = datetime.now(KST).isoformat(timespec="seconds")
        work.parent.mkdir(parents=True, exist_ok=True)
        work.write_text(json.dumps({"config": config, "started_at": started}, ensure_ascii=False) + "\n", encoding="utf-8")

    qa_cases = [c for c in cases if c["mode"] == "qa"]
    neg = [c for c in cases if c["category"] in NEGATIVE and ref.get(c["id"])]
    answerable = [c for c in qa_cases if c["category"] not in NEGATIVE and not ref.get(c["id"])]
    items = [("main", c, None) for c in qa_cases] + [("retry", c, p) for c in neg for p in answerable
                                                    if p["actor_dept"] == c["actor_dept"]]

    rules = values.load(cfg.rules_path)
    pool, engine = await open_engine(cfg)
    async with pool.acquire() as conn:
        loaded = {p: await departments.load(conn, p) for p in ("it_service", "ecommerce")}
    retrieved = []
    real_retrieve = engine.retrieve

    async def spy(q, ctx):  # 계획이 실제로 쓴 마지막 검색 결과(근거 후보·lexemes)를 평가가 따로 잡음
        res = await real_retrieve(q, ctx)
        retrieved.append(res)
        return res
    engine.retrieve = spy
    llm = Recording(LLMClient(cfg))

    with work.open("a", encoding="utf-8") as f:
        for kind, case, prev in items:
            key = f"{kind}:{case['id']}" + (f"<{prev['id']}" if prev else "")
            if key in done:
                continue
            ctx = actor(case)
            names, qa_depts = loaded[ctx.active_pack]
            case_status = service.analyze(case["query"], rules, qa_depts.name_parts).case_status
            retrieved.clear()
            llm.calls.clear()
            t = time.perf_counter()
            r = await ask(engine, llm, rules, names, qa_depts, case, prev, ctx)
            benchmark = bool(case["expected_doc_ids"]) and all(meta[d]["knowledge_pack"] == "benchmark"
                                                               for d in case["expected_doc_ids"])
            row = {"key": key, "kind": kind, "id": case["id"], "previous": prev["id"] if prev else None,
                   "behavior": case["expected_behavior"], "result": r.result, "latency_s": round(time.perf_counter() - t, 2),
                   "llm": llm.calls[0] if llm.calls else None, "question": case["query"], "benchmark": benchmark,
                   # LLM 을 부르지 않은 근거 부족 = R3(LLM 앞) 판정. 고른 문서를 넘기지 않으므로 다른 길은 특정 건 처리
                   # 현황 조회(찾지 않고 막음)뿐이라 그것은 R3 에서 빼고 따로 셈
                   "case_status": case_status,
                   "r3_insufficient": not llm.calls and r.result == "insufficient_evidence" and not case_status,
                   **inspect(case, r, retrieved[-1] if retrieved else None, llm.calls[0] if llm.calls else None,
                             cfg.qa_evidence_chunks, lambda d: visible(meta, case, d), current_tag, titles)}
            row["correct"] = None if kind == "retry" or benchmark else judge(case["expected_behavior"], row,
                                                                              case["expected_doc_ids"])
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            done[key] = row
            print(f"{key} {r.result} {row['latency_s']}s", flush=True)
    await llm.llm.close()
    await pool.close()

    summary = summarize(list(done.values()), ref)
    out.parent.mkdir(parents=True, exist_ok=True)
    text_free = ("question", "sentences")  # 결과에는 질의·답변 글을 담지 않음(작업 파일에만)
    questions = [{k: v for k, v in r.items() if k not in text_free} for r in done.values() if r["kind"] == "main"]
    out.write_text(json.dumps({"config": {**config, "started_at": started,
                                          "finished_at": datetime.now(KST).isoformat(timespec="seconds")},
                               "summary": summary, "questions": questions}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ok", "must", "table", "results", "follow_up")}, ensure_ascii=False, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(asyncio.run(main(outside_repo(Path(sys.argv[1])),
                              Path(sys.argv[2] if len(sys.argv) > 2 else "eval/results/qa_eval.json"),
                              Path(sys.argv[3] if len(sys.argv) > 3 else "eval/results/retrieval_eval.json"))))
