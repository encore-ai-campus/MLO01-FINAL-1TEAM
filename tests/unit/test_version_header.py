"""원천 머리말 읽기·내용 해시·버전 ID·버전 머리말(경계 계약 5.4·5.2·4.10·5.1)."""
import json

import pytest

from rag_api.shared.version_header import HASH_META_KEYS, content_hash, parse_source, render_header, split_header, version_id

SRC = """---
doc_id: IT-FIN-008
title: "지출결의서 작성, 대금 정산"   # 주석
doc_type: official_policy
source_tier: 1
knowledge_pack: it_service
dept_owner: FIN
allowed_depts: [FIN, "HR", 'PLN']
effective_from: "2026-12-01"
step_next: []
glossary_terms: [증빙]
empty:
version_tag: v1.2
---

# 지출결의서

본문 첫 줄
"""


def test_parse_source_reads_like_the_refining_script():
    meta, body = parse_source(SRC)
    assert meta["title"] == "지출결의서 작성, 대금 정산"  # 따옴표 안 쉼표는 그대로, 닫는 따옴표 뒤 주석은 버림
    assert meta["source_tier"] == 1 and meta["allowed_depts"] == ["FIN", "HR", "PLN"] and meta["effective_from"] == "2026-12-01"
    assert meta["empty"] == "" and meta["step_next"] == [] and meta["glossary_terms"] == ["증빙"]
    assert (meta["chapter_id"], meta["chapter_name"], meta["step_prev"], meta["effective_to"], meta["source_org"]) == \
        ("ch00", "공통·미분류", [], "", "")  # 선택 키 기본값(5.2)
    assert body == "# 지출결의서\n\n본문 첫 줄"  # 앞뒤 공백 자름
    assert parse_source(SRC.replace("\n", "\r\n"))[0] == meta  # CRLF 머리말도 읽음
    with pytest.raises(ValueError):
        parse_source("# 머리말 없음")


def test_parse_source_edge_values_follow_the_script_not_yaml():
    meta, _ = parse_source('---\na: "\nb: 2026-01-01\nc: "x" # 주석\nd: [a, , b]\ne: 012\nf: x # 주석\ng: x#y\n---\n본문')
    assert meta["a"] == "" and meta["b"] == "2026-01-01" and meta["c"] == "x" and meta["d"] == ["a", "b"]
    assert meta["e"] == 12 and meta["f"] == "x" and meta["g"] == "x#y"


def test_content_hash_formula_and_what_it_ignores():
    meta, body = parse_source(SRC)
    h = content_hash(meta, body)
    payload = {k: sorted(str(x) for x in v) if isinstance(v, list) else v for k in HASH_META_KEYS for v in [meta.get(k, "")]}
    import hashlib
    assert h == hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                                + "\n---\n" + body).encode()).hexdigest()
    assert content_hash({**meta, "allowed_depts": ["PLN", "FIN", "HR"]}, body) == h  # 목록 순서
    assert content_hash({**meta, "version_tag": "v9.9", "confidence_score": 0.1, "empty": "x"}, body) == h  # 해시 키가 아님
    assert content_hash({**meta, "required_form_id": "STD-FIN-001"}, body) != h  # 17번째 키
    assert content_hash(meta, body + " ") != h
    assert len(HASH_META_KEYS) == 17


DUMMY_VERSIONS = [  # 메인기획 더미 7건(가상 값, 11차 회신 꾸러미 documents_dummy.yaml): doc_id, version_tag, content_hash, version_id
    ("DUMMY-PUB-001", "v1.0", "85f84df320b945542a656e0918bd2cc4634a2e54001b72517e2dd51d81a62a6d",
     "d2a9a85a-b1c4-5e59-9c30-8ff735d030b9"),
    ("DUMMY-COMMON-001", "v1.0", "b0c4a514fc4375f5f6b5bae5b6364e8a9794b707971f53206f94fc21b167a6b9",
     "e09fa5e8-5612-5f86-9724-82116f1c4b5a"),
    ("DUMMY-HR-001", "v1.0", "4a35b8a3c10391fea7f38445b78b1525ed3815eb6ef8d29255973eeb592a893b",
     "3db25414-62cf-584f-99c2-592f9f890fba"),
    ("DUMMY-OPS-001", "v1.1", "f18ea19ddf66be0931d93b6544601b768a103aa2bf5cb32568eb13d32cfe5420",
     "c7827fb5-2e2a-531e-becb-878a510ec614"),
    ("DUMMY-OPS-001", "v1.2", "f650592ed082e4a19f58ad9d51881fb84944d498a9944e9c6e96ab4e9b3002a0",
     "331d0c71-276c-5f54-829c-a7e8977fb3a8"),
    ("LAW-API-DUMMY-001", "v1.0", "9e432d49ed13ed6a9345d453c54e888b3ad04c91d826b904b0d24a0909ce0c4b",
     "ae58b406-a026-58f4-a021-87c30d66a4ba"),
    ("DUMMY-STD-001", "v1.0", "afbf8fe3a8bd1892aef51e2a56b2634edca5246e0cc67c81d476e7392d3b6621",
     "c383c09d-df80-53b7-8550-0174be1bced0"),
]


@pytest.mark.parametrize("doc_id, tag, h, vid", DUMMY_VERSIONS)
def test_version_id_matches_the_planner_dummy_samples(doc_id, tag, h, vid):
    assert version_id(doc_id, tag, h) == vid


def test_render_header_round_trips_through_split_header():
    meta = {"doc_id": "D-1", "title": 'He said "x": #1 [a]', "source_tier": 1, "allowed_depts": ["HR", "FIN"],
            "effective_from": "2026-12-01", "source_org": None, "confidence_score": 0.82, "review_reason": "LOW_CONFIDENCE",
            "not_a_header_key": "버림"}
    raw = render_header(meta) + "# 제목\n본문"
    head, body = split_header(raw)
    assert list(head) == ["doc_id", "title", "doc_type", "source_tier", "knowledge_pack", "chapter_id", "chapter_name",
                          "dept_owner", "allowed_depts", "effective_from", "source_org", "source_url", "classification_method",
                          "confidence_score", "review_reason"]  # 5.1 의 키·순서 고정
    assert {k: head[k] for k in meta if k in head} == {k: v for k, v in meta.items() if k != "not_a_header_key"}
    assert head["doc_type"] is None and body == "# 제목\n본문"
