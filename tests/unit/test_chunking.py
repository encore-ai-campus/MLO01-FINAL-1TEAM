"""공유 청킹 함수(경계 계약 5.3). 합성 원문으로 경계 사례를 봅니다. 길이는 글자 수 그대로 맞춰 경계 한 글자 차이를 가립니다.
마지막 시험은 메인기획 정제 원문·조각 파일이 있을 때만 돕니다(환경 변수, 없으면 건너뜀. CI 는 건너뜀)."""
import os
import re
from pathlib import Path

import pytest
import yaml

from rag_api.shared.chunking import chunk_markdown

META = {"doc_id": "T-DOC-001", "title": "시험 규정", "version_tag": "v1.0", "chapter_id": "ch01", "chapter_name": "시험 장"}


def chunks(body: str, **meta):
    return chunk_markdown({**META, **meta}, body)


def lens(cs):
    return [len(c.content) for c in cs]


def doc(*units: str, chapter: str = "장") -> str:
    """한 장 안의 조항들. 조항 i 의 본문은 units[i]."""
    return f"## {chapter}\n" + "\n".join(f"### 조{i}\n{u}" for i, u in enumerate(units))


def table(rows: int, row_len: int) -> str:
    """머리 2줄(11자) + 같은 길이 행. 전체 길이 = 11 + rows * (row_len + 1)."""
    return "\n".join(["| a |", "|---|", *("| " + "가" * (row_len - 4) + " |" for _ in range(rows))])


def test_chapters_and_clauses_keep_original_titles_and_ids():
    cs = chunks("# 문서 제목\n\n## 제1장 총칙\n\n### 제1조 (목적)\n\n" + "가" * 300 + "\n\n### 제2조 (정의)\n" + "나" * 300
                + "\n## 제2장 정산\n### 제3조 (기한)\n" + "다" * 300)
    assert [c.section_path for c in cs] == ["제1장 총칙 > 제1조 (목적)", "제1장 총칙 > 제2조 (정의)", "제2장 정산 > 제3조 (기한)"]
    assert [c.chunk_id for c in cs] == [f"T-DOC-001::v1.0::c00{i}" for i in (1, 2, 3)]
    assert [c.content for c in cs] == ["가" * 300, "나" * 300, "다" * 300]  # 제목 줄은 빠지고 본문만
    assert cs[0].embed_text == f"[시험 규정 | ch01 시험 장 | 제1장 총칙 > 제1조 (목적)]\n{'가' * 300}"


def test_paths_without_chapter_or_clause_and_headings_that_are_not_split_points():
    cs = chunks("앞 글 " + "가" * 300 + "\n### 조항만\n" + "나" * 300 + "\n## 장만\n" + "다" * 300 + "\n#### 소제목\n##붙은 제목\n" + "라" * 10)
    assert [c.section_path for c in cs] == ["본문", "조항만", "장만"]
    assert cs[2].content == "다" * 300 + "\n#### 소제목\n##붙은 제목\n" + "라" * 10  # `####`·`##` 뒤 공백 없음은 본문


def test_chunk_id_follows_version_tag_and_chapter_defaults_come_from_embed_text():
    cs = chunk_markdown({"doc_id": "T-DOC-002", "title": "제목", "version_tag": "v2.3"}, "## 장\n본문")
    assert cs[0].chunk_id == "T-DOC-002::v2.3::c001"
    assert cs[0].embed_text == "[제목 | ch00 공통·미분류 | 장]\n본문"


def test_crlf_body_gives_the_same_chunks_as_lf():
    body = doc("가" * 300, "나" * 100, "다" * 100, "라" * 900 + "\n\n" + "마" * 900)
    assert chunks(body.replace("\n", "\r\n")) == chunks(body)


def test_units_in_different_chapters_never_merge_but_same_chapter_ones_do():
    cs = chunks("## A\n### a\n" + "가" * 100 + "\n## B\n### b\n" + "나" * 100)
    assert [c.section_path for c in cs] == ["A > a", "B > b"]
    (c,) = chunks("## A\n### a\n" + "가" * 100 + "\n### b\n" + "나" * 100 + "\n### c\n" + "다" * 100)
    assert c.section_path == "A > a & b & c" and c.content == "가" * 100 + "\n\n" + "나" * 100 + "\n\n" + "다" * 100


@pytest.mark.parametrize("prev, cur, merged", [
    (279, 400, True), (280, 400, False),  # 앞 조각 280 미만이면 합침
    (300, 219, True), (300, 220, False),  # 지금 단위 220 미만이면 합침
    (279, 597, True), (279, 598, False),  # 합쳐(+4) 880 이하
    (730, 149, True), (730, 150, False),  # 150 미만 꼬리는 상한이 1,050 으로 늘어남(150 이면 880 그대로)
    (897, 149, True), (898, 149, False),  # 1,050 이하 / 1,051
])
def test_merge_boundaries(prev, cur, merged):
    cs = chunks(doc("가" * prev, "나" * cur))
    if merged:
        assert [c.section_path for c in cs] == ["장 > 조0 & 조1"] and cs[0].content == "가" * prev + "\n\n" + "나" * cur
    else:
        assert [c.section_path for c in cs] == ["장 > 조0", "장 > 조1"] and lens(cs) == [prev, cur]


def test_over_880_splits_at_blank_line_paragraphs_and_continues_numbering():
    a, b = ("가" * 400, "나" * 300)
    (one,) = chunks(doc(f"{a}\n\n{b}\n\n" + "다" * 176))  # 400+2+300+2+176 = 880 → 나누지 않음
    assert one.section_path == "장 > 조0"
    cs = chunks(doc(f"{a}\n\n{b}\n\n" + "다" * 177, "라" * 300))  # 881 → 나눔
    assert [c.section_path for c in cs] == ["장 > 조0 (분할 1/2)", "장 > 조0 (분할 2/2)", "장 > 조1"]
    assert [c.content for c in cs][:2] == [f"{a}\n\n{b}", "다" * 177]
    assert [c.chunk_id[-4:] for c in cs] == ["c001", "c002", "c003"]
    assert cs[1].embed_text.startswith("[시험 규정 | ch01 시험 장 | 장 > 조0 (분할 2/2)]\n")


@pytest.mark.parametrize("c, n", [(478, 2), (479, 3)])
def test_a_new_parts_first_paragraph_is_counted_without_the_blank_line_allowance(c, n):
    """기준 스크립트의 셈: 나눈 뒤 새 조각의 첫 문단은 글자 수 그대로, 이어 붙는 문단은 +2. 그래서 400+478+2 = 880 은 한 조각."""
    cs = chunks(doc("가" * 800 + "\n\n" + "나" * 400 + "\n\n" + "다" * c))
    assert len(cs) == n and cs[1].content.startswith("나" * 400) and (n == 2) == (len(cs[1].content) == 880)


def test_paragraphs_are_cut_of_surrounding_spaces_and_counting_restarts_after_a_big_table():
    cs = chunks(doc("가" * 500 + "\n\n  " + "나" * 500))
    assert [c.content for c in cs] == ["가" * 500, "나" * 500]
    cs = chunks(doc("가" * 400 + "\n\n" + table(12, 98) + "\n\n" + "나" * 400 + "\n\n" + "다" * 400))
    assert lens(cs)[0] == 400 and lens(cs)[-1] == 400 + 2 + 400 and len(cs) == 4  # 가, 표 둘, 나+다


def test_long_paragraph_without_blank_line_is_not_split():
    assert lens(chunks(doc("가" * 2000))) == [2000]
    assert lens(chunks(doc("가" * 1500 + "\n\n" + "나" * 100))) == [1500, 100]  # 꼬리는 150 미만이나 합치면 1,100 넘음


@pytest.mark.parametrize("b, c, merged", [
    (800, 149, True), (800, 150, False),  # 나눈 마지막 글 문단이 150 미만이면 앞 조각에 붙음
    (949, 149, True), (950, 149, False),  # 합쳐(+2) 1,100 이하 / 1,101
])
def test_orphan_tail_of_a_split_joins_the_previous_part(b, c, merged):
    cs = chunks(doc("가" * 800 + "\n\n" + "나" * b + "\n\n" + "다" * c))
    n = 2 if merged else 3
    assert [x.section_path for x in cs] == [f"장 > 조0 (분할 {i}/{n})" for i in range(1, n + 1)]
    assert lens(cs) == ([800, b + 2 + c] if merged else [800, b, c])


def test_orphan_tail_is_not_joined_to_or_as_a_table():
    plain_tail = chunks(doc("가" * 998 + "\n\n" + "나" * 100))
    assert lens(plain_tail) == [998 + 2 + 100]  # 글 꼬리는 붙음(1,100 이하)
    assert lens(chunks(doc("가" * 998 + "\n\n|" + "나" * 99))) == [998, 100]  # 꼬리가 표면 같은 크기여도 안 붙음
    assert lens(chunks(doc("가" * 900 + "\n\n" + "나" * 100))) == [900 + 2 + 100]
    assert lens(chunks(doc("|" + "가" * 899 + "\n\n" + "나" * 100))) == [900, 100]  # 앞이 표면 안 붙음


def test_table_up_to_1100_stays_in_one_chunk_and_bigger_ones_split_by_row_with_header():
    (one,) = chunks(doc("가" * 50 + "\n\n" + table(23, 42)))  # 표 1,000자 → 앞 문단과 함께 한 조각(1,052 > 880 이어도)
    assert len(one.content) == 50 + 2 + 1000
    (whole,) = chunks(doc(table(11, 98)))
    assert len(whole.content) == 1100 and whole.section_path == "장 > 조0"
    big = table(11, 98).replace("가" * 94, "가" * 95, 1)  # 1,101자: 마지막 행이 못 들어감
    cs = chunks(doc(big))
    head, rows = big.split("\n", 2)[:2], big.split("\n")[2:]
    assert [c.section_path for c in cs] == ["장 > 조0 (분할 1/2)", "장 > 조0 (분할 2/2)"]
    assert [c.content.split("\n")[:2] for c in cs] == [head, head]  # 조각마다 머리 2줄
    assert [c.content.split("\n")[2:] for c in cs] == [rows[:10], rows[10:]]  # 행 순서 그대로
    assert lens(chunks(doc(table(22, 98)))) == [1100, 1100]  # 두 번째 조각부터도 머리 2줄 포함 정확히 1,100자까지


def test_big_table_after_text_is_cut_from_it_and_a_table_without_header_stays_whole():
    cs = chunks(doc("가" * 100 + "\n\n" + table(12, 98)))
    assert lens(cs) == [100, 1100, 110]  # 행이 11개(정확히 1,100자)까지 한 조각에 들어감
    assert len(cs) == 3 and cs[1].content.startswith("| a |\n|---|") and cs[2].content.startswith("| a |\n|---|")
    no_head = "\n".join("|" + "가" * 50 + "|" for _ in range(30))  # 구분선(---) 줄이 없는 1,500자 표
    assert lens(chunks(doc(no_head))) == [len(no_head)]


@pytest.mark.parametrize("n, fails", [(999, False), (1000, True)])
def test_more_than_999_chunks_fail_the_document(n, fails):
    body = "\n".join(f"### 조{i}\n" + "가" * 300 for i in range(n))  # 300자씩이라 서로 합쳐지지 않음
    if fails:
        with pytest.raises(ValueError, match="T-DOC-001"):
            chunks(body)
    else:
        cs = chunks(body)
        assert len(cs) == 999 and cs[-1].chunk_id == "T-DOC-001::v1.0::c999"


# --- 실데이터 대조(메인기획 정제 원문·조각 파일). 환경 변수가 없으면 건너뜀 ---

@pytest.mark.skipif(not (os.environ.get("RAG_REFINED_DOCS") and os.environ.get("RAG_CHUNKS_YAML")), reason="RAG_REFINED_DOCS·RAG_CHUNKS_YAML 이 없음")
def test_chunk_markdown_matches_the_published_chunks_files():
    """`RAG_REFINED_DOCS`(정제 원문 폴더) 전체를 쪼갠 결과가 `RAG_CHUNKS_YAML`(조각 파일. 현행·보관 두 파일이면 `:` 로 이음)의
    같은 `doc_id::version_tag` 조각과 같은지. 끝 줄바꿈은 YAML 블록 표기 때문에 파일 쪽에만 있어 뗍니다. 문서·조각 건수에 묶지 않음."""
    want: dict[str, list[dict]] = {}
    for f in os.environ["RAG_CHUNKS_YAML"].split(os.pathsep):
        for c in yaml.safe_load(Path(f).read_text(encoding="utf-8")):
            want.setdefault(c["chunk_id"].rsplit("::", 1)[0], []).append(c)
    seen = 0
    for path in sorted(Path(os.environ["RAG_REFINED_DOCS"]).rglob("*.md")):
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        if path.name.lower() == "readme.md" or path.name.startswith((".", "_")) or not m:
            continue
        meta = {"version_tag": "v1.0", "chapter_id": "ch00", "chapter_name": "공통·미분류", **yaml.safe_load(m.group(1))}
        got = [(c.chunk_id, c.section_path, c.content, c.embed_text) for c in chunk_markdown(meta, m.group(2).strip())]
        exp = [tuple(c[k].rstrip("\n") for k in ("chunk_id", "section_path", "content", "embed_text"))
               for c in want.get(f"{meta['doc_id']}::{meta['version_tag']}", [])]
        assert got == exp, path.name
        seen += 1
    assert seen and seen == len(want)
