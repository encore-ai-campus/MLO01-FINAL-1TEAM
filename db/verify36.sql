SELECT version();
SHOW shared_preload_libraries;
SELECT extname, extversion FROM pg_extension ORDER BY extname;
DO $verify$
BEGIN
  IF (SELECT count(*) FROM pg_extension WHERE extname IN ('pgcrypto','vector','pg_textsearch')) <> 3 THEN RAISE EXCEPTION 'Missing extension'; END IF;
  IF (SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relname IN ('departments','users','auth_audit_logs','documents','document_versions','chunks','ontology_concepts','answer_feedbacks') AND relkind IN ('r','p')) <> 8 THEN RAISE EXCEPTION 'Missing core table'; END IF;
  IF EXISTS (SELECT FROM (VALUES ('auth_audit_logs'), ('answer_feedbacks')) AS tables(name) WHERE (SELECT count(*) FROM pg_partition_tree(('public.' || name)::regclass) WHERE isleaf) <> 4) THEN RAISE EXCEPTION 'Missing partition'; END IF;
  IF (SELECT count(*) FROM departments) <> 10 OR (SELECT count(*) FROM ontology_concepts) <> 12 OR EXISTS (SELECT FROM users) THEN RAISE EXCEPTION 'Unexpected seed count'; END IF;
  IF (SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname='embedding') IS DISTINCT FROM 'halfvec(1024)' THEN RAISE EXCEPTION 'Invalid embedding column'; END IF;
  IF NOT EXISTS (SELECT FROM pg_indexes WHERE indexname='idx_chunks_embedding_hnsw' AND indexdef LIKE '%USING hnsw (embedding halfvec_cosine_ops)%') OR NOT EXISTS (SELECT FROM pg_indexes WHERE indexname='idx_chunks_bm25' AND indexdef LIKE '%USING bm25 (tsv_to_text(tsv))%') THEN RAISE EXCEPTION 'Missing vector or BM25 index'; END IF;
  IF (SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_users_updated_at','trg_documents_updated_at') AND tgenabled='O') <> 2 THEN RAISE EXCEPTION 'Missing trigger'; END IF;
  IF EXISTS (SELECT FROM (VALUES ('rag_app'),('rag_pipeline')) AS roles(role_name) CROSS JOIN (VALUES ('users'),('documents'),('document_versions')) AS tables(table_name) WHERE has_table_privilege(role_name,table_name,'DELETE')) THEN RAISE EXCEPTION 'Unexpected DELETE privilege'; END IF;
END
$verify$;
BEGIN;
INSERT INTO users(email,name,updated_at) VALUES ('local@example.invalid','Synthetic check','2000-01-01');
UPDATE users SET name='Updated check' WHERE email='local@example.invalid';
DO $verify$
BEGIN
  IF NOT EXISTS (SELECT FROM users WHERE email='local@example.invalid' AND updated_at=now()) THEN RAISE EXCEPTION 'Trigger did not fire'; END IF;
  BEGIN
    UPDATE users SET role='INVALID' WHERE email='local@example.invalid';
    RAISE EXCEPTION 'Role constraint did not fire';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    UPDATE users SET dept_code='MISSING' WHERE email='local@example.invalid';
    RAISE EXCEPTION 'Foreign key did not fire';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
END
$verify$;
INSERT INTO auth_audit_logs(action,created_at) VALUES ('LOGIN','2027-02-03');
INSERT INTO answer_feedbacks(query_text,feedback_type,created_at) VALUES ('test','like','2027-02-03');
DO $verify$
BEGIN
  IF NOT EXISTS (SELECT FROM ONLY auth_audit_logs_2027_q1) OR NOT EXISTS (SELECT FROM ONLY answer_feedbacks_2027_q1) THEN RAISE EXCEPTION 'Incorrect partition routing'; END IF;
END
$verify$;
ROLLBACK;
