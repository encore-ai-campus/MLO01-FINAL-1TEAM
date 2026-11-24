"""문서 쪼개기 (경계 계약 5.3). 메인기획 정제 스크립트 `build_pgvector_chunks.py` 의 `build_chunks_for_doc` 과 결과가 같아야 합니다.

머리말 읽기·내용 해시(5.2)·발행(4.2)은 이 모듈의 일이 아닙니다. 읽어 온 머리말(`meta`)과 머리말 뒤 본문을 받아 쪼개기만 합니다.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rag_api.shared.embedding import embed_text

TARGET_MIN, TARGET_MAX = 280, 880  # 앞 조각이 MIN 미만이거나 지금 단위가 SHORT_UNIT 미만이면 합침(합쳐서 MAX 이하)
SHORT_UNIT = 220
TABLE_MAX = 1100  # 표 문단은 여기까지 한 조각에 담고, 넘으면 행 단위로 나눔
ORPHAN_TAIL_CHARS = 150  # 이보다 짧은 꼬리는 합침 상한이 TAIL_MERGE_MAX_CHARS 로 늘고, 나눈 끝 글 문단이면 앞 조각에 붙음
TAIL_MERGE_MAX_CHARS = 1050
MAX_CHUNKS = 999  # 한 버전의 조각 수 상한(조각 ID 의 `c{NNN}` 세 자리)


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


def _split_table(table: str) -> list[str]:
    """TABLE_MAX 를 넘는 표를 행 단위로 나누고 조각마다 머리 2줄을 다시 붙임. 머리가 표 모양이 아니거나 행이 없으면 그대로."""
    lines = [ln for ln in table.splitlines() if ln.strip()]
    if len(lines) < 3 or "---" not in lines[1]:
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
    return [*parts, "\n".join([head, *rows])]


def _split_block(text: str) -> list[str]:
    """TARGET_MAX 를 넘는 단위를 빈 줄 문단 경계에서 나눔. 빈 줄 없는 긴 글 문단은 그대로 둠."""
    if len(text) <= TARGET_MAX:
        return [text]
    parts: list[str] = []
    cur: list[str] = []
    size = 0  # ponytail: 새 조각의 첫 문단은 +2 없이 세는 기준 스크립트의 셈을 그대로 따름(결과를 같게)
    for p in filter(None, (q.strip() for q in re.split(r"\n{2,}", text))):
        table = p.startswith("|")
        if table and len(p) > TABLE_MAX:
            if cur:
                parts.append("\n\n".join(cur))
                cur, size = [], 0
            parts += _split_table(p)
        elif cur and size + len(p) + 2 > (TABLE_MAX if table else TARGET_MAX):
            parts.append("\n\n".join(cur))
            cur, size = [p], len(p)
        else:
            cur.append(p)
            size += len(p) + 2
    if cur:
        parts.append("\n\n".join(cur))
    if (len(parts) >= 2 and len(parts[-1]) < ORPHAN_TAIL_CHARS and not parts[-1].startswith("|")
            and not parts[-2].startswith("|") and len(parts[-2]) + 2 + len(parts[-1]) <= TABLE_MAX):
        parts[-2:] = [f"{parts[-2]}\n\n{parts[-1]}"]
    return parts


def chunk_markdown(meta: Mapping[str, Any], body: str) -> list[Chunk]:
    """`meta` 는 `doc_id`·`title`·`version_tag` 가 필요하고 `chapter_id`·`chapter_name` 은 없으면 기본값(embed_text). 조각이 999개를 넘으면 ValueError."""
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

    chunks: list[Chunk] = []
    for path, text in merged:
        parts = _split_block(text)
        for i, part in enumerate(parts, 1):
            section = f"{path} (분할 {i}/{len(parts)})" if len(parts) > 1 else path
            chunks.append(Chunk(f"{meta['doc_id']}::{meta['version_tag']}::c{len(chunks) + 1:03d}", section, part,
                                embed_text(meta["title"], meta.get("chapter_id"), meta.get("chapter_name"), section, part)))
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"{meta['doc_id']}: 조각 {len(chunks)}개 > {MAX_CHUNKS}")
    return chunks
