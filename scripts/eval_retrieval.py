"""Golden QA v2 검색 평가(knowledge_engine 구현 계획 4.4). 개발 DB(scripts/seed_dev_db.py)에 `retrieve` 를 돌립니다.

- 지표는 `docs/research/최신 데이터 재측정.md` 와 같게 셉니다. 청크 순위를 문서 순위로 바꿔
  Recall@5 = 정답 문서 중 상위 5문서에 든 비율, MRR@10 = 상위 10문서 중 첫 정답 순위의 역수.
  평가 문항은 열람 차단·근거 없는 질문·이전 버전 보기를 뺀 것, "닿는 문항"은 정답 문서가 모두 질문자 열람·팩 안인 것.
- 질문자: actor_dept 가 PUBLIC 이면 비로그인, 아니면 그 부서의 ACTIVE 직원. 팩은 문항 pack(없으면 it_service).
- 근거 부족 판정 재측정(`docs/research/근거 부족 판정 실측.md`): 후보 상위 10 청크의 IDF 덮음 최댓값 < 0.40 또는
  벡터 1위 코사인 < 0.21 이면 근거 부족. 질의 토큰이 없으면 코사인만. 기준값은 rules.md 의 값이고 바꾸지 않습니다.
- 벡터 1위 코사인은 엔진이 따로 넘기는 값(`RetrievalResult.vector_top1_cosine`)을 씁니다. 결과 상한은 설정 기본값입니다.
- 권한 밖 결과는 정제본 documents.yaml 의 열람·팩으로 따로 셉니다(구현과 다른 판정으로 확인).

환경 변수: DATABASE_URL(개발 DB), RAG_GOLDEN_QA(golden_qa_v2.yaml), RAG_DATA_DIR(rag_ready 폴더),
EMBED_BASE_URL·EMBED_MODEL·EMBED_TIMEOUT_S 등(shared.config)
사용: PYTHONPATH=apps python scripts/eval_retrieval.py [결과 경로(기본 eval/results/retrieval_eval.json)]
결과에는 집계와 문항 ID 단위 값만 담습니다(질의·본문 없음).
"""
import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import asyncpg
import yaml

from rag_api.auth.context import AuthContext
from rag_api.knowledge_engine import term_stats
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.knowledge_engine.types import RetrievalQuery
from rag_api.settings import ontology
from rag_api.shared.config import Config
from rag_api.shared.current_version import today_kst
from rag_api.shared.embedding import OpenAIEmbeddingClient
from rag_api.shared.tokenize import tokenize_ko

NOW = datetime(2026, 11, 21)


def actor(case: dict) -> AuthContext:
    pack = case.get("pack") or "it_service"
    if case["actor_dept"] == "PUBLIC":
        return AuthContext(principal_type="anonymous", active_pack=pack, snapshot_at=NOW)
    return AuthContext(principal_type="user", status="ACTIVE", dept_code=case["actor_dept"], role="EMPLOYEE",
                       active_pack=pack, snapshot_at=NOW)


def doc_order(chunk_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(c.split("::")[0] for c in chunk_ids))


def recall_rr(expected: list[str], docs: list[str]) -> tuple[float, float]:
    e = set(expected)
    first = next((i for i, d in enumerate(docs[:10], 1) if d in e), None)
    return len(e & set(docs[:5])) / len(e), (1 / first if first else 0.0)


def mean(xs) -> float | None:
    xs = list(xs)
    return round(statistics.mean(xs), 3) if xs else None


def load_meta(data: Path) -> dict[str, dict]:
    """정제본 documents.yaml: 문서 ID → 현행(active) 판, 없으면 첫 판. eval_qa 도 씀."""
    meta = {}
    for d in sorted(yaml.safe_load((data / "documents.yaml").read_text(encoding="utf-8")),
                    key=lambda d: d["version_status"] != "active"):
        meta.setdefault(d["doc_id"], d)
    return meta


def visible(meta: dict[str, dict], case: dict, doc_id: str) -> bool:
    """구현과 따로 적은 열람·팩 판정(재측정 문서 1절). eval_qa 도 씀."""
    d, a = meta[doc_id], case["actor_dept"]
    groups = {"PUBLIC"} if a == "PUBLIC" else {"PUBLIC", "ALL_EMP", a}
    return bool(set(d["allowed_depts"]) & groups) and d["knowledge_pack"] in ("base", case.get("pack") or "it_service")


async def open_engine(cfg: Config) -> tuple[asyncpg.Pool, KnowledgeEngine]:
    """개발 DB 풀, 온톨로지 사전·검색 통계 적재, 임베딩 예열, KnowledgeEngine. eval_qa 도 씀."""
    pool = await asyncpg.create_pool(cfg.database_url)
    async with pool.acquire() as conn:
        await ontology.reload_if_changed(conn)
        await term_stats.refresh(conn)
    # 모델을 올리는 첫 호출(약 1초)이 질의 시간 제한(EMBED_TIMEOUT_S)에 걸리지 않게 미리 부름
    OpenAIEmbeddingClient(cfg.embed_base_url, cfg.embed_model, 60, cfg.embed_api_key).embed_query("준비")
    embedder = OpenAIEmbeddingClient(cfg.embed_base_url, cfg.embed_model, cfg.embed_timeout_s, cfg.embed_api_key)
    return pool, KnowledgeEngine(pool, embedder, cfg)


async def main(out: Path) -> None:
    cfg = Config.from_env()
    meta = load_meta(Path(os.environ["RAG_DATA_DIR"]))
    cases = yaml.safe_load(Path(os.environ["RAG_GOLDEN_QA"]).read_text(encoding="utf-8"))["cases"]
    pool, engine = await open_engine(cfg)
    stats = term_stats.current()
    today = today_kst()  # 평가가 자정을 넘겨도 한 실행 안에서는 같은 날짜

    rows = []
    for case in cases:
        ctx = actor(case)
        row = {"id": case["id"], "category": case["category"], "mode": case["mode"], "actor": case["actor_dept"],
               "pack": ctx.active_pack, "expected": case["expected_doc_ids"]}
        for strategy in ("hybrid", "graph_hybrid"):
            t = time.perf_counter()
            res = await engine.retrieve(RetrievalQuery(case["query"], strategy=strategy), ctx, today=today)
            ms = (time.perf_counter() - t) * 1000
            docs = doc_order([c.chunk_id for c in res.chunks])
            key = "" if strategy == "hybrid" else "graph_"
            row[key + "top_docs"] = docs[:10]
            if case["expected_doc_ids"]:
                row[key + "recall5"], row[key + "rr10"] = recall_rr(case["expected_doc_ids"], docs)
            if strategy != "hybrid":
                continue
            tokens = tokenize_ko(case["query"])
            coverage = term_stats.top_coverage(res, tokens, stats, cfg.insufficient_coverage_chunks)
            cosine = res.vector_top1_cosine
            row.update(
                latency_ms=round(ms, 1), degraded=res.status.degraded, chunks=len(res.chunks),
                leaks=sum(not visible(meta, case, c.doc.doc_id) for c in res.chunks),
                # 정제본의 현행 버전 태그와 다른 버전의 청크(구현과 따로 판정)
                non_current_chunks=sum(c.chunk_id.split("::")[1] != meta[c.doc.doc_id]["version_tag"] for c in res.chunks),
                coverage=None if coverage is None else round(coverage, 3),
                cosine=None if cosine is None else round(cosine, 3),
                insufficient=term_stats.insufficient(res, tokens, stats, cfg))  # qa 와 같은 판정 함수
        if case["id"] == "GQA-ACL-003":
            row["discoverable_hints"] = [h.doc_id for h in await engine.discoverable_hints(case["query"], ctx, today=today)]
        rows.append(row)
    await pool.close()

    ev = [r for r in rows if r["category"] not in ("acl_block", "insufficient_evidence")
          and r["mode"] != "history_view" and r["expected"]]
    reach = [r for r in ev if all(visible(meta, next(c for c in cases if c["id"] == r["id"]), e) for e in r["expected"])]
    neg = [r for r in rows if r["category"] in ("insufficient_evidence", "acl_block")]
    answered = [r for r in ev if set(r["expected"]) & set(r["top_docs"][:5])]
    lat = sorted(r["latency_ms"] for r in rows)
    by_cat = defaultdict(list)
    for r in ev:
        by_cat[r["category"]].append(r["recall5"])
    summary = {
        "evaluated": len(ev), "reachable": len(reach),
        **{s: {name: {"recall5": mean(r[key + "recall5"] for r in rs), "mrr10": mean(r[key + "rr10"] for r in rs)}
               for name, rs in (("all", ev), ("reachable", reach))}
           for s, key in (("hybrid", ""), ("graph_hybrid", "graph_"))},
        "recall5_by_category": {k: mean(v) for k, v in sorted(by_cat.items())},
        "unreachable": [r["id"] for r in ev if r not in reach],
        "reachable_misses": [r["id"] for r in reach if r["recall5"] < 1],
        "latency_ms": {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))], "max": lat[-1]},
        "degraded": sum(r["degraded"] for r in rows),
        "leaks": sum(r["leaks"] for r in rows), "non_current_chunks": sum(r["non_current_chunks"] for r in rows),
        "acl_003": next(({k: r[k] for k in ("leaks", "chunks", "top_docs", "discoverable_hints")}
                         for r in rows if r["id"] == "GQA-ACL-003"), None),
        "r3": {"thresholds": {"coverage": cfg.insufficient_coverage_min, "cosine": cfg.insufficient_cosine_min},
               "should_block": len(neg), "blocked": sum(r["insufficient"] for r in neg),
               "answered": len(answered), "wrongly_blocked": sum(r["insufficient"] for r in answered),
               "missed": [r["id"] for r in neg if not r["insufficient"]],
               "wrong": [r["id"] for r in answered if r["insufficient"]]},
        "term_stats": {"n": stats.n, "vocabulary": len(stats.df)},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": {"keyword_top_k": cfg.keyword_top_k, "vector_top_k": cfg.vector_top_k,
                                          "rrf_k": cfg.rrf_k, "result_top_n": cfg.result_top_n,
                                          "embed_model": cfg.embed_model, "expansion": cfg.ontology_expansion_enabled,
                                          "insufficient_coverage_chunks": cfg.insufficient_coverage_chunks},
                               "summary": summary, "questions": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "eval/results/retrieval_eval.json")))
