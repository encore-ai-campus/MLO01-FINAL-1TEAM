"""원천 머리말 읽기·내용 해시·버전 ID·버전 머리말 (경계 계약 5.4·5.2·4.10·5.1).

파이프라인·사규 빌더·시드 들여오기와 서버가 같은 함수를 씁니다. 해시·ID 는 메인기획 정제 스크립트·조각 규격과 같은 값을 냅니다.
머리말 키 검사(5.4 의 필수 키·허용 값)는 이 모듈의 일이 아닙니다(적재하는 쪽이 함).
"""
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

import yaml

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.S)
HEADER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
HASH_META_KEYS = ("doc_id", "title", "doc_type", "source_tier", "knowledge_pack", "chapter_id", "chapter_name",
                  "dept_owner", "allowed_depts", "effective_from", "effective_to", "step_prev", "step_next",
                  "glossary_terms", "required_form_id", "source_org", "source_url")  # 5.2
HEADER_KEYS = ("doc_id", "title", "doc_type", "source_tier", "knowledge_pack", "chapter_id", "chapter_name", "dept_owner",
               "allowed_depts", "effective_from", "source_org", "source_url", "classification_method", "confidence_score",
               "review_reason")  # 5.1 의 순서


def _scalar_or_list(raw: str) -> Any:
    val = raw.strip()
    if val.startswith("[") and val.endswith("]"):
        return [item for part in val[1:-1].split(",") if (item := part.strip().strip('"').strip("'"))]
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):  # 스크립트 그대로(`"` 한 글자면 빈 값)
        return val[1:-1]
    return int(val) if val.isdigit() else val


def parse_source(text: str) -> tuple[dict[str, Any], str]:
    """원천 파일(텍스트 모드로 읽은 글) → (선택 키 기본값을 채운 머리말, 앞뒤 공백을 자른 본문). 정제 스크립트 `parse_markdown_doc` 의 읽기와 같음.
    YAML 일반 파서가 아님: 한 줄 `키: 값` 만 읽고, `[a, b]` 는 문자열 목록, 따옴표 없는 숫자는 정수, 그 밖은 문자열(5.2). 머리말이 없으면 ValueError."""
    m = FRONTMATTER.match(text)
    if not m:
        raise ValueError("머리말(---)이 없습니다")
    meta: dict[str, Any] = {}
    for line in m[1].splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        key, val = s.split(":", 1)
        val = val.strip()
        if val.startswith('"'):
            end = val.find('"', 1)
            val = val[:end + 1] if end > 0 else val  # 닫는 따옴표 뒤(주석 등)는 버림
        else:
            val = re.sub(r"\s+#.*$", "", val)
        meta[key.strip()] = _scalar_or_list(val)
    meta.setdefault("chapter_id", "ch00")
    meta.setdefault("chapter_name", "공통·미분류")
    for k in ("step_prev", "step_next", "glossary_terms"):
        if not isinstance(meta.get(k), list):
            meta[k] = []
    for k in ("effective_from", "effective_to", "source_org", "source_url"):
        meta.setdefault(k, "")
    return meta, m[2].strip()


def content_hash(meta: Mapping[str, Any], body: str) -> str:
    """5.2. `meta`·`body` 는 parse_source 결과. 목록은 정렬해 넣어 순서가 달라도 같은 값."""
    payload = {k: sorted(str(x) for x in v) if isinstance(v, list) else v for k in HASH_META_KEYS for v in [meta.get(k, "")]}
    meta_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{meta_json}\n---\n{body}".encode("utf-8")).hexdigest()


def version_id(doc_id: str, version_tag: str, content_hash: str) -> str:
    """4.10. PostgreSQL·Silver·Gold 가 같은 값을 씀."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rag-policy://document-version/{doc_id}::{version_tag}::{content_hash}"))


def render_header(meta: Mapping[str, Any]) -> str:
    """5.1 버전 머리말(`---` 두 줄 포함). 키와 순서는 HEADER_KEYS 로 고정하고 없는 키는 null."""
    head = yaml.safe_dump({k: meta.get(k) for k in HEADER_KEYS}, allow_unicode=True, sort_keys=False,
                          default_flow_style=None, width=10**6)
    return f"---\n{head}---\n"


def split_header(raw: str) -> tuple[dict, str]:
    """`raw_markdown` → (머리말, 본문). 머리말이 없으면 ({}, 그대로), 있는데 읽을 수 없으면 ({}, 머리말 뗀 본문)."""
    m = HEADER.match(raw)
    if not m:
        return {}, raw
    try:
        meta = yaml.safe_load(m[1])
    except yaml.YAMLError:
        meta = None
    return (meta if isinstance(meta, dict) else {}), raw[m.end():]
