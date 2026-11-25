"""문서 쪼개기 (경계 계약 5.3). 메인기획 조각 규격(2026-11-25)의 `build_chunks_for_doc` 과 결과가 같아야 합니다.

머리말 읽기·내용 해시(5.2)·발행(4.2)은 이 모듈의 일이 아닙니다. 읽어 온 머리말(`meta`)과 머리말 뒤 본문을 받아 쪼개기만 합니다.
규칙의 순서·셈은 규격을 그대로 옮겼습니다(결과를 같게). 다른 점은 토크나이저를 인자로 받는 것뿐입니다(추정값 대체 없음, 3.4).
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tokenizers import Tokenizer

from rag_api.shared.embed_tokens import count_body_tokens, count_embed_tokens
from rag_api.shared.embedding import embed_text

TARGET_MIN, TARGET_MAX = 280, 880  # 앞 조각이 MIN 미만이거나 지금 단위가 SHORT_UNIT 미만이면 합침(합쳐서 MAX 이하)
SHORT_UNIT = 220
TABLE_MAX = 1100  # 표 문단은 여기까지 한 조각에 담고, 넘으면 행 단위로 나눔(규격의 int(880 * 1.25))
ORPHAN_TAIL_CHARS = 150  # 이보다 짧은 단위는 합침 상한이 TAIL_MERGE_MAX_CHARS 로 늘고, 나눈 조각이면 이웃에 붙음
TAIL_MERGE_MAX_CHARS = 1050
EMBED_MAX_TOKENS = 1024  # 임베딩 입력 상한(3.4). 시작·끝 특수 토큰과 `passage: ` 포함
MIN_BODY_BUDGET = 250  # 머리가 아무리 길어도 본문 예산은 이만큼
MAX_CHUNKS = 999  # 한 버전의 조각 수 상한(조각 ID 의 `c{NNN}` 세 자리)
_SENTENCE_END = re.compile(r"(?<=[.!?다요])\s+")
_HEADING_ONLY = re.compile(r"#{1,4}\s+.*")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    section_path: str
    content: str
    embed_text: str


def _sections(body: str) -> list[tuple[str, str]]:
    """`##`(장)·`###`(조항) 제목으로 나눔. `# ` 제목 줄은 버리고, 경로는 `## 장 > ### 조항`(제목이 없으면 `본문`)."""
    out: list[tuple[str, str]] = []
    h2 = h3 = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            out.append((" > ".join(filter(None, (h2, h3))) or "본문", text))

    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith(("## ", "### ")):
            flush()
            buf.clear()
            title = line[3:].strip()  # `## 장` 은 3째 글자부터, `### 조항` 은 앞 공백이 strip 으로 빠짐
            h2, h3 = (title, "") if line.startswith("## ") else (h2, title)
        else:
            buf.append(line)
    flush()
    return out


def _is_table(text: str) -> bool:
    return text.lstrip().startswith("|")


def _split_table(table: str) -> list[str]:
    """TABLE_MAX 를 넘는 표를 행 단위로 나누고 조각마다 머리 2줄을 다시 붙임. 머리가 표 모양이 아니면 그대로."""
    lines = [ln for ln in table.splitlines() if ln.strip()]
    if len(table) <= TABLE_MAX or len(lines) < 3 or not _is_table(lines[0]) or "---" not in lines[1]:
        return [table]
    head = "\n".join(lines[:2])
    parts: list[str] = []
    rows: list[str] = []
    size = len(head)
    for row in lines[2:]:
        if rows and size + len(row) + 1 > TABLE_MAX:
            parts.append("\n".join([head, *rows]))
            rows, size = [], len(head)
        rows.append(row)
        size += len(row) + 1
    return [*parts, "\n".join([head, *rows])] if rows else parts or [table]


def _split_paragraph(p: str) -> list[str]:
    """TARGET_MAX 를 넘는 표 아닌 문단: 줄 → 문장 끝 → 마지막 공백(그 앞이 절반 미만이면 글자) 경계로 자르고, 줄바꿈으로 다시 묶음."""
    units: list[str] = []
    for ln in (ln for ln in p.splitlines() if ln.strip()):
        if len(ln) <= TARGET_MAX:
            units.append(ln)
            continue
        for s in filter(None, (s.strip() for s in _SENTENCE_END.split(ln))):
            while len(s) > TARGET_MAX:
                cut = s[:TARGET_MAX].rsplit(" ", 1)[0]
                if len(cut) < TARGET_MAX // 2:
                    cut = s[:TARGET_MAX]
                units.append(cut.strip())
                s = s[len(cut):].strip()
            if s:
                units.append(s)
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for u in units:
        if cur and size + len(u) + 1 > TARGET_MAX:
            out.append("\n".join(cur))
            cur, size = [u], len(u)
        else:
            cur.append(u)
            size += len(u) + (1 if size else 0)
    if cur:
        out.append("\n".join(cur))
    return out or [p]


def _split_block(text: str) -> list[str]:
    """TARGET_MAX 를 넘는 단위를 문단 경계에서 나누고, 150자 미만이거나 제목 줄만 있는 조각을 이웃에 붙임."""
    if len(text) <= TARGET_MAX:
        return [text]
    paragraphs: list[str] = []
    for p in filter(None, (q.strip() for q in re.split(r"\n{2,}", text))):
        paragraphs += _split_paragraph(p) if not _is_table(p) and len(p) > TARGET_MAX else [p]
    parts: list[str] = []
    cur: list[str] = []
    size = 0  # 새 조각의 첫 문단은 +2 없이 세는 규격의 셈을 그대로 따름(결과를 같게)
    for p in paragraphs:
        table = _is_table(p)
        if table and len(p) > TABLE_MAX:
            if cur:
                parts.append("\n\n".join(cur))
                cur, size = [], 0
            parts += _split_table(p)
            continue
        if cur and size + len(p) + 2 > (TABLE_MAX if table else TARGET_MAX):
            parts.append("\n\n".join(cur))
            cur, size = [p], len(p)
        else:
            cur.append(p)
            size += len(p) + 2
    if cur:
        parts.append("\n\n".join(cur))

    merged: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        heading = bool(_HEADING_ONLY.fullmatch(p))
        orphan = len(p) < ORPHAN_TAIL_CHARS or heading
        cap = TABLE_MAX + 250 if (heading or len(p) < 40) else TABLE_MAX
        if orphan and i + 1 < len(parts) and len(p) + 2 + len(parts[i + 1]) <= cap:
            parts[i + 1] = f"{p}\n\n{parts[i + 1]}"  # 다음 조각 앞에 붙임
        elif orphan and merged and (not _is_table(merged[-1]) or len(p) < 40) and len(merged[-1]) + 2 + len(p) <= cap:
            merged[-1] = f"{merged[-1]}\n\n{p}"  # 안 되면 앞 조각 뒤에
        elif not heading:
            merged.append(p)  # 어디에도 못 붙인 제목 줄 조각은 버림
        i += 1
    return merged


def _split_tokens(tok: Tokenizer, part: str, budget: int) -> list[str]:
    """본문 토큰(특수 토큰 없음)이 budget 을 넘는 조각을 더 나눔. 표는 행(넘치는 행은 낱말), 글은 줄 → 문장 → 낱말 → 글자."""
    n = lambda s: count_body_tokens(tok, s)  # noqa: E731
    if n(part) <= budget:
        return [part]
    lines = [ln for ln in part.splitlines() if ln.strip()]
    if len(lines) >= 3 and _is_table(lines[0]) and "---" in lines[1]:
        head = lines[0] + "\n" + lines[1]
        head_toks = n(head)
        row_budget = max(180, budget - head_toks - 10)
        tables: list[str] = []
        rows: list[str] = []
        toks = head_toks
        for row in lines[2:]:
            rt = n(row)
            if rt > row_budget:
                if rows:
                    tables.append(head + "\n" + "\n".join(rows))
                    rows, toks = [], head_toks
                seg: list[str] = []
                for w in row.strip("| ").split(" "):
                    if seg and n("| " + " ".join(seg + [w]) + " |") > row_budget:
                        tables.append(head + "\n| " + " ".join(seg) + " |")
                        seg = [w]
                    else:
                        seg.append(w)
                if seg:
                    tables.append(head + "\n| " + " ".join(seg) + " |")
                continue
            if rows and toks + rt + 1 > budget:
                tables.append(head + "\n" + "\n".join(rows))
                rows, toks = [row], head_toks + rt
            else:
                rows.append(row)
                toks += rt + 1
        if rows:
            tables.append(head + "\n" + "\n".join(rows))
        return tables or [part]

    units: list[str] = []
    for ln in lines:
        if n(ln) <= budget:
            units.append(ln)
            continue
        for s in filter(None, (s.strip() for s in _SENTENCE_END.split(ln))):
            if n(s) <= budget:
                units.append(s)
                continue
            words: list[str] = []
            for w in s.split(" "):
                if words and n(" ".join(words + [w])) > budget:
                    units.append(" ".join(words))
                    words = [w]
                else:
                    words.append(w)
            if words:
                rem = " ".join(words)
                while n(rem) > budget:
                    cut = max(100, int(len(rem) * budget / max(1, n(rem))) - 20)
                    units.append(rem[:cut].strip())
                    rem = rem[cut:].strip()
                if rem:
                    units.append(rem)
    out: list[str] = []
    cur: list[str] = []
    for u in units:
        if cur and n("\n".join(cur + [u])) > budget:
            out.append("\n".join(cur))
            cur = [u]
        else:
            cur.append(u)
    if cur:
        out.append("\n".join(cur))
    if (len(out) >= 2 and len(out[-1]) < ORPHAN_TAIL_CHARS and not _is_table(out[-1]) and not _is_table(out[-2])
            and n(out[-2] + "\n" + out[-1]) <= budget):
        out[-2:] = [f"{out[-2]}\n{out[-1]}"]
    return out or [part]


def chunk_markdown(meta: Mapping[str, Any], body: str, tok: Tokenizer, max_tokens: int = EMBED_MAX_TOKENS) -> list[Chunk]:
    """`meta` 는 `doc_id`·`title`·`version_tag` 가 필요하고 `chapter_id`·`chapter_name` 은 없으면 기본값(embed_text).
    `tok` 은 임베딩 모델 토크나이저(embed_tokens.load_tokenizer), `max_tokens` 는 설정 `EMBED_MAX_TOKENS`. 조각이 999개를 넘으면 ValueError."""
    merged: list[tuple[str, str]] = []
    for path, text in _sections(body):
        if merged:
            prev_path, prev = merged[-1]
            cap = TAIL_MERGE_MAX_CHARS if len(text) < ORPHAN_TAIL_CHARS else TARGET_MAX
            if (prev_path.split(" > ")[0] == path.split(" > ")[0] and len(prev) + len(text) + 4 <= cap
                    and (len(prev) < TARGET_MIN or len(text) < SHORT_UNIT)):  # 같은 장 안에서만
                merged[-1] = (f"{prev_path} & {path.split(' > ')[-1]}", f"{prev}\n\n{text}")
                continue
        merged.append((path, text))

    law = str(meta["doc_id"]).startswith("LAW-API-")
    header = lambda section: embed_text(meta["title"], meta.get("chapter_id"), meta.get("chapter_name"), section, "")  # noqa: E731
    chunks: list[Chunk] = []
    for path, text in merged:
        if law and "부칙" in path:  # 법령 부칙 절은 넣지 않음
            continue
        lines = text.splitlines()
        lead = lines[0].strip()[:120] if lines else ""
        prefix = f"[상위 조문] {lead}\n\n" if law and len(lead) >= 12 else ""
        # 머리는 가장 긴 꼴(분할 99/99)로 셈. 머리 문자열 뒤 줄바꿈 + 접두어까지 같이 센 뒤 2를 더 뺌(규격의 셈)
        budget = max(MIN_BODY_BUDGET, max_tokens - count_embed_tokens(tok, header(f"{path} (분할 99/99)") + prefix) - 2)
        parts = [q for p in _split_block(text) for q in _split_tokens(tok, p, budget)]
        if prefix and len(parts) > 1:
            parts = [parts[0]] + [p if p.startswith("[상위 조문]") else prefix + p for p in parts[1:]]
            if len(parts[0].strip()) < 40 and lead.startswith(parts[0].strip()[:15]):
                parts = parts[1:]  # 첫 줄만 남은 첫 조각은 다음 조각들의 접두어와 겹치므로 뺌
        for i, part in enumerate(parts, 1):
            section = f"{path} (분할 {i}/{len(parts)})" if len(parts) > 1 else path
            chunks.append(Chunk(f"{meta['doc_id']}::{meta['version_tag']}::c{len(chunks) + 1:03d}", section, part,
                                embed_text(meta["title"], meta.get("chapter_id"), meta.get("chapter_name"), section, part)))
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"{meta['doc_id']}: 조각 {len(chunks)}개 > {MAX_CHUNKS}")
    return chunks
