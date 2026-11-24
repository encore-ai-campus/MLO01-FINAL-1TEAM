"""개발 DB 시드가 원문(raw_markdown)을 경계 계약 5.1 모양으로 넣는지(원문 보기 API 가 `rag_dev` 에서 되게). DB·임베딩 없이 함수만.
문서 값은 모두 시험용 합성 값."""
import re

import pytest
import yaml
from seed_dev_db import read_bodies, read_body, render_raw

ROW = {"doc_id": "T-DOC-008", "title": "시험 규정: 작성 및 정산", "doc_type": "official_policy", "source_tier": 1,
       "knowledge_pack": "it_service", "chapter_id": "ch99", "chapter_name": "시험 장 이름", "dept_owner": "FIN",
       "allowed_depts": ["FIN", "HR"], "effective_from": "2026-12-01", "source_org": "", "source_url": "",
       "classification_method": "manual_frontmatter", "version_tag": "v1.1"}
SOURCE = '---\ndoc_id: "T-DOC-008"\ntitle: "시험 규정"\nversion_tag: "v1.1"\nallowed_depts: ["FIN"]\n---\n\n# 제목\n\n### 제1조\n본문\n'


def test_read_body_drops_header_normalizes_newlines_and_strips(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes(SOURCE.replace("\n", "\r\n").encode())  # 정제 원문 중에는 CRLF 도 있음
    assert read_body(path) == "# 제목\n\n### 제1조\n본문"
    (tmp_path / "b.md").write_text("머리말 없는 글 \n", encoding="utf-8")
    assert read_body(tmp_path / "b.md") == "머리말 없는 글"


def test_read_bodies_follows_each_rows_relative_path(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "new.md").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "x" / "old.md").write_text("---\ndoc_id: T-DOC-008\n---\n옛 판\n", encoding="utf-8")
    docs = [{"relative_path": "x/new.md"}, {"relative_path": "x/old.md"}]
    assert read_bodies(tmp_path, docs) == ["# 제목\n\n### 제1조\n본문", "옛 판"]  # 행 순서대로
    with pytest.raises(FileNotFoundError):
        read_bodies(tmp_path, [*docs, {"relative_path": "x/없음.md"}])


def test_read_body_of_a_missing_file_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):  # main 이 DB 에 붙기 전에 이렇게 멈춤
        read_body(tmp_path / "없는 파일.md")


def split_header(raw):
    """머리말 YAML 과 본문으로 나눔(경계 계약 5.1)."""
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", raw, re.S)
    return yaml.safe_load(m[1]), raw[m.end():]


def test_render_raw_has_contract_5_1_header(tmp_path):
    path = tmp_path / "a.md"
    path.write_text(SOURCE, encoding="utf-8")
    body = read_body(path)
    raw = render_raw(ROW, body)
    meta, rest = split_header(raw)
    assert rest == body  # 머리말을 떼면 정제 본문 그대로
    assert list(meta) == ["doc_id", "title", "doc_type", "source_tier", "knowledge_pack", "chapter_id", "chapter_name", "dept_owner",
                          "allowed_depts", "effective_from", "source_org", "source_url", "classification_method", "confidence_score",
                          "review_reason"]  # 5.1 의 15키·순서
    assert meta["title"] == "시험 규정: 작성 및 정산" and meta["allowed_depts"] == ["FIN", "HR"] and meta["effective_from"] == "2026-12-01"
    assert meta["source_org"] is None and meta["source_url"] is None and meta["review_reason"] is None
    assert len(raw) - len(rest) > 250  # 판 목록이 본문 앞부분만 읽어도 머리말이 닫히는 길이대


def test_render_raw_is_valid_yaml_for_awkward_values():
    raw = render_raw({**ROW, "title": 'He said "x": #1 [a]', "source_org": "(주) 사내: 제정", "allowed_depts": ["ALL_EMP"]}, "본문")
    head = raw.split("---\n")[1]
    assert yaml.safe_load(head)["title"] == 'He said "x": #1 [a]' and split_header(raw)[0]["source_org"] == "(주) 사내: 제정"
