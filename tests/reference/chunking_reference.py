"""시험 기준 답: 메인기획 조각 규격(`chunking_spec_11th.py`, 2026-11-25, 11차 회신 인터페이스 꾸러미)의 쪼개기 부분.

`tests/unit/test_chunking.py` 가 더미·합성 원문으로 `rag_api.shared.chunking.chunk_markdown` 과 결과를 대조할 때만 씁니다.
앱 코드는 이 파일을 import 하지 않습니다. 실제 데이터에는 쓰지 않습니다.

원본에서 바꾼 곳(규칙 줄은 그대로)
- 쪼개기 함수만 남김. 내용 해시(`compute_content_hash`)·버전 ID(`compute_version_id`)는 뺌(따로 시험함)
- 토크나이저: import 때 환경 변수로 읽고 못 찾으면 추정값(900)으로 바꾸던 부분을 지우고, `set_tokenizer` 로 넣게 함
- `build_chunks_for_doc` 의 토큰 상한: 추정값 대체가 없으므로 `EMBED_MAX_TOKENS` 하나만 씀
"""
from __future__ import annotations

import re
from typing import Any

TARGET_MIN_CHARS = 280
TARGET_MAX_CHARS = 880
ORPHAN_TAIL_CHARS = 150
TAIL_MERGE_MAX_CHARS = 1050
PASSAGE_PREFIX = "passage: "
EMBED_MAX_TOKENS = 1024

_TOKENIZER: Any = None


def set_tokenizer(tok: Any) -> None:
    global _TOKENIZER
    _TOKENIZER = tok


def estimate_tokens(text: str) -> int:
    """KURE-v3 토큰 수(특수 토큰 없음)."""
    return len(_TOKENIZER.encode(text, add_special_tokens=False).ids)


def count_full_embed_tokens(embed_text: str) -> int:
    """서버 입력 토큰 수: 'passage: ' + embed_text 를 토크나이저 기본값으로 셈(원본 그대로)."""
    return len(_TOKENIZER.encode(PASSAGE_PREFIX + embed_text.rstrip("\n")).ids)


def split_sections(body: str) -> list[tuple[str, str]]:
    """Split body by ## and ### headings, keeping table blocks intact."""
    lines = body.splitlines()
    h2_title = ""
    h3_title = ""
    buf: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        content = "\n".join(buf).strip()
        if not content:
            return
        if h2_title and h3_title:
            sec_path = f"{h2_title} > {h3_title}"
        elif h2_title:
            sec_path = h2_title
        elif h3_title:
            sec_path = h3_title
        else:
            sec_path = "본문"
        sections.append((sec_path, content))

    for line in lines:
        if line.startswith("# ") and not line.startswith("## "):
            continue
        if line.startswith("## "):
            flush()
            buf = []
            h2_title = line[3:].strip()
            h3_title = ""
            continue
        if line.startswith("### "):
            flush()
            buf = []
            h3_title = line[4:].strip()
            continue
        buf.append(line)
    flush()
    return sections


def split_oversized_table(table_text: str, table_limit: int) -> list[str]:
    """Split Markdown tables exceeding table_limit by row while re-attaching the 2-line header."""
    if len(table_text) <= table_limit:
        return [table_text]
    lines = [ln for ln in table_text.splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[0].lstrip().startswith("|") or "---" not in lines[1]:
        return [table_text]

    header_block = "\n".join(lines[:2])
    chunks: list[str] = []
    cur_rows: list[str] = []
    cur_len = len(header_block)

    for row in lines[2:]:
        row_cost = len(row) + 1
        if cur_rows and cur_len + row_cost > table_limit:
            chunks.append(header_block + "\n" + "\n".join(cur_rows))
            cur_rows = [row]
            cur_len = len(header_block) + row_cost
        else:
            cur_rows.append(row)
            cur_len += row_cost

    if cur_rows:
        chunks.append(header_block + "\n" + "\n".join(cur_rows))
    return chunks or [table_text]


def split_single_paragraph(p: str, max_chars: int) -> list[str]:
    """Split an oversized single non-table paragraph at line or sentence boundaries."""
    if len(p) <= max_chars:
        return [p]
    lines = [ln for ln in p.splitlines() if ln.strip()]
    units: list[str] = []
    for ln in lines:
        if len(ln) <= max_chars:
            units.append(ln)
        else:
            sents = re.split(r"(?<=[.!?다요])\s+", ln)
            for s in sents:
                s = s.strip()
                if not s:
                    continue
                while len(s) > max_chars:
                    cut = s[:max_chars].rsplit(" ", 1)[0]
                    if len(cut) < max_chars // 2:
                        cut = s[:max_chars]
                    units.append(cut.strip())
                    s = s[len(cut):].strip()
                if s:
                    units.append(s)
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        if cur and cur_len + len(u) + 1 > max_chars:
            out.append("\n".join(cur))
            cur = [u]
            cur_len = len(u)
        else:
            cur.append(u)
            cur_len += len(u) + (1 if cur_len else 0)
    if cur:
        out.append("\n".join(cur))
    return out or [p]


def split_oversized_block(text: str, max_chars: int) -> list[str]:
    """Split large prose+table blocks at paragraph/line boundaries; absorb <150-char orphan tails."""
    table_limit = int(max_chars * 1.25)
    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs: list[str] = []
    for raw_p in raw_paragraphs:
        p = raw_p.strip()
        if not p:
            continue
        if not p.lstrip().startswith("|") and len(p) > max_chars:
            paragraphs.extend(split_single_paragraph(p, max_chars))
        else:
            paragraphs.append(p)

    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paragraphs:
        is_table = p.lstrip().startswith("|")
        if is_table and len(p) > table_limit:
            if cur:
                parts.append("\n\n".join(cur))
                cur = []
                cur_len = 0
            parts.extend(split_oversized_table(p, table_limit))
            continue
        limit = table_limit if is_table else max_chars
        if cur and cur_len + len(p) + 2 > limit:
            parts.append("\n\n".join(cur))
            cur = [p]
            cur_len = len(p)
        else:
            cur.append(p)
            cur_len += len(p) + 2
    if cur:
        parts.append("\n\n".join(cur))

    merged_parts: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        is_heading = bool(re.fullmatch(r"#{1,4}\s+.*", p))
        is_orphan = len(p) < ORPHAN_TAIL_CHARS or is_heading
        max_merge = table_limit + 250 if (is_heading or len(p) < 40) else table_limit
        if is_orphan and i + 1 < len(parts) and len(p) + 2 + len(parts[i + 1]) <= max_merge:
            parts[i + 1] = f"{p}\n\n{parts[i + 1]}"
            i += 1
            continue
        if is_orphan and merged_parts and (not merged_parts[-1].lstrip().startswith("|") or len(p) < 40) and len(merged_parts[-1]) + 2 + len(p) <= max_merge:
            merged_parts[-1] = f"{merged_parts[-1]}\n\n{p}"
            i += 1
            continue
        if is_heading:
            i += 1
            continue
        merged_parts.append(p)
        i += 1
    return merged_parts


def split_to_token_budget(part: str, max_tokens: int) -> list[str]:
    """Guarantee every sub-chunk stays strictly <= max_tokens."""
    if estimate_tokens(part) <= max_tokens:
        return [part]
    lines = [ln for ln in part.splitlines() if ln.strip()]
    is_table = len(lines) >= 3 and lines[0].lstrip().startswith("|") and "---" in lines[1]
    if is_table:
        header_block = lines[0] + "\n" + lines[1]
        header_toks = estimate_tokens(header_block)
        row_budget = max(180, max_tokens - header_toks - 10)
        out_tables: list[str] = []
        cur_rows: list[str] = []
        cur_toks = header_toks
        for row in lines[2:]:
            rtoks = estimate_tokens(row)
            if rtoks > row_budget:
                if cur_rows:
                    out_tables.append(header_block + "\n" + "\n".join(cur_rows))
                    cur_rows = []
                    cur_toks = header_toks
                words = row.strip("| ").split(" ")
                seg: list[str] = []
                for w in words:
                    cand = "| " + " ".join(seg + [w]) + " |"
                    if seg and estimate_tokens(cand) > row_budget:
                        out_tables.append(header_block + "\n| " + " ".join(seg) + " |")
                        seg = [w]
                    else:
                        seg.append(w)
                if seg:
                    out_tables.append(header_block + "\n| " + " ".join(seg) + " |")
                continue
            if cur_rows and cur_toks + rtoks + 1 > max_tokens:
                out_tables.append(header_block + "\n" + "\n".join(cur_rows))
                cur_rows = [row]
                cur_toks = header_toks + rtoks
            else:
                cur_rows.append(row)
                cur_toks += rtoks + 1
        if cur_rows:
            out_tables.append(header_block + "\n" + "\n".join(cur_rows))
        return out_tables or [part]

    units: list[str] = []
    for ln in lines:
        if estimate_tokens(ln) <= max_tokens:
            units.append(ln)
        else:
            sents = re.split(r"(?<=[.!?다요])\s+", ln)
            for s in sents:
                s = s.strip()
                if not s:
                    continue
                if estimate_tokens(s) <= max_tokens:
                    units.append(s)
                else:
                    words = s.split(" ")
                    buf_w: list[str] = []
                    for w in words:
                        if buf_w and estimate_tokens(" ".join(buf_w + [w])) > max_tokens:
                            units.append(" ".join(buf_w))
                            buf_w = [w]
                        else:
                            buf_w.append(w)
                    if buf_w:
                        rem = " ".join(buf_w)
                        while estimate_tokens(rem) > max_tokens:
                            cut = max(100, int(len(rem) * max_tokens / max(1, estimate_tokens(rem))) - 20)
                            units.append(rem[:cut].strip())
                            rem = rem[cut:].strip()
                        if rem:
                            units.append(rem)
    out: list[str] = []
    cur: list[str] = []
    for u in units:
        cand = "\n".join(cur + [u])
        if cur and estimate_tokens(cand) > max_tokens:
            out.append("\n".join(cur))
            cur = [u]
        else:
            cur.append(u)
    if cur:
        out.append("\n".join(cur))
    if len(out) >= 2 and len(out[-1]) < ORPHAN_TAIL_CHARS and not out[-1].lstrip().startswith("|") and not out[-2].lstrip().startswith("|") and estimate_tokens(out[-2] + "\n" + out[-1]) <= max_tokens:
        tail = out.pop()
        out[-1] = f"{out[-1]}\n{tail}"
    return out or [part]


def build_chunks_for_doc(meta: dict[str, Any], body: str, exclude_appendix: bool | None = None) -> list[dict[str, Any]]:
    """Build deterministic chunk dicts aligned with Reply 11 & db/init.sql v1.12."""
    is_law_api = meta["doc_id"].startswith("LAW-API-")
    should_exclude_appendix = is_law_api if exclude_appendix is None else exclude_appendix

    raw_sections = split_sections(body)
    merged: list[tuple[str, str]] = []

    for sec_path, content in raw_sections:
        same_chapter = bool(merged) and merged[-1][0].split(" > ")[0] == sec_path.split(" > ")[0]
        prev_too_short = bool(merged) and len(merged[-1][1]) < TARGET_MIN_CHARS
        cur_too_short = len(content) < 220
        cur_orphan_tail = len(content) < ORPHAN_TAIL_CHARS
        merge_cap = TAIL_MERGE_MAX_CHARS if cur_orphan_tail else TARGET_MAX_CHARS
        can_merge_len = bool(merged) and (len(merged[-1][1]) + len(content) + 4 <= merge_cap)

        if merged and can_merge_len and same_chapter and (prev_too_short or cur_too_short):
            prev_path, prev_content = merged[-1]
            tail = sec_path.split(" > ")[-1]
            merged[-1] = (f"{prev_path} & {tail}", f"{prev_content}\n\n{content}")
        else:
            merged.append((sec_path, content))

    chunks: list[dict[str, Any]] = []
    idx = 1
    for sec_path, content in merged:
        if should_exclude_appendix and "부칙" in sec_path:
            continue
        raw_subparts = split_oversized_block(content, TARGET_MAX_CHARS)
        subparts: list[str] = []
        base_header = f"[{meta['title']} | {meta['chapter_id']} {meta['chapter_name']} | {sec_path} (분할 99/99)]"
        lead_line = content.splitlines()[0].strip()[:120] if content.splitlines() else ""
        lead_prefix = f"[상위 조문] {lead_line}\n\n" if (is_law_api and len(lead_line) >= 12) else ""
        part_budget = max(250, EMBED_MAX_TOKENS - count_full_embed_tokens(base_header + "\n" + lead_prefix) - 2)
        for rp in raw_subparts:
            subparts.extend(split_to_token_budget(rp, part_budget))
        if lead_prefix and len(subparts) > 1:
            subparts = [subparts[0]] + [
                p if p.startswith("[상위 조문]") else f"{lead_prefix}{p}"
                for p in subparts[1:]
            ]
            if len(subparts[0].strip()) < 40 and lead_line.startswith(subparts[0].strip()[:15]):
                subparts = subparts[1:]
        for sub_idx, part in enumerate(subparts, start=1):
            suffix = f" (분할 {sub_idx}/{len(subparts)})" if len(subparts) > 1 else ""
            full_section = f"{sec_path}{suffix}"
            embed_header = f"[{meta['title']} | {meta['chapter_id']} {meta['chapter_name']} | {full_section}]"
            chunks.append(
                {
                    "chunk_id": f"{meta['doc_id']}::{meta['version_tag']}::c{idx:03d}",
                    "doc_id": meta["doc_id"],
                    "title": meta["title"],
                    "version_tag": meta["version_tag"],
                    "version_status": meta["version_status"],
                    "chapter_id": meta["chapter_id"],
                    "chapter_name": meta["chapter_name"],
                    "knowledge_pack": meta["knowledge_pack"],
                    "dept_owner": meta["dept_owner"],
                    "allowed_depts": meta["allowed_depts"],
                    "source_tier": meta["source_tier"],
                    "is_official": meta["source_tier"] == 1,
                    "section_path": full_section,
                    "char_len": len(part),
                    "content": part,
                    "embed_text": f"{embed_header}\n{part}",
                    "chunk_index": idx,
                }
            )
            idx += 1
    return chunks
