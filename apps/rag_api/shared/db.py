"""DB 보조. 지금은 임시 BM25 DDL 하나."""

# 계약 2.6 의 BM25 확장·함수·식 인덱스. init.sql v1.11 에는 아직 없어(6차 후속 2번으로 들어갈 예정)
# 개발 DB 적재(scripts/seed_dev_db.py)와 통합 시험이 init.sql 다음에 돌립니다. init.sql 에 들어가면 지웁니다.
BM25_DDL = """
CREATE EXTENSION IF NOT EXISTS pg_textsearch;
CREATE OR REPLACE FUNCTION tsv_to_text(t tsvector) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT coalesce(string_agg(u.lexeme, ' ' ORDER BY p), '')
  FROM unnest(t) AS u, unnest(u.positions) AS p
$$;
CREATE INDEX IF NOT EXISTS idx_chunks_bm25 ON chunks USING bm25 (tsv_to_text(tsv))
  WITH (text_config = 'simple', k1 = 1.2, b = 0.75);
"""
