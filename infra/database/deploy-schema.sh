#!/usr/bin/env bash
# Run through sudo once. Bash and the local Docker socket are used throughout.
set -Eeuo pipefail
umask 077
export LC_ALL=C
unset PGHOST PGHOSTADDR PGPORT PGUSER PGPASSWORD PGSERVICE PGSERVICEFILE PGOPTIONS

case "${1:---check}" in
    --check|--apply) mode="${1:---check}" ;;
    *) echo "Usage: sudo bash infra/database/deploy-schema.sh [--check|--apply]" >&2; exit 2 ;;
esac
[[ $# -le 1 && $EUID -eq 0 ]] || { echo "Run once through sudo, with at most one mode argument." >&2; exit 2; }

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
init="$root/db/init.sql"
deploy_dir=/opt/database
container=rag-postgres
database=rag_dev
digest=sha256:c72b4cb8aaa0a52c0870c2b0a48173c595ee9bef0f3d8f92c3f546c69fef3567
expected_sql=3caea68c626d17f81daeae397f1204bf52b30328465f6f5b1497d04fe9b97dea
docker=(docker --host unix:///var/run/docker.sock)

db_exec() { "${docker[@]}" exec -i --user postgres "$container" "$@"; }
psql_db() {
    local db=$1; shift
    db_exec psql -X --no-password -v ON_ERROR_STOP=1 --username postgres --dbname "$db" "$@"
}
backup=''
scratch=''
phase=preflight
cleanup() {
    local code=$?
    trap - EXIT
    if [[ -n $scratch ]]; then
        if ! db_exec dropdb --no-password --username postgres "$scratch" >>"$backup/restore.log" 2>&1; then
            printf 'Temporary restore database needs manual cleanup: %s\n' "$scratch" >&2
            [[ $code != 0 ]] || code=1
        fi
    fi
    if [[ $code != 0 ]]; then
        printf 'Stopped (phase %s). Backup/log directory: %s\n' "$phase" "${backup:-not created}" >&2
        if [[ $phase == apply ]]; then
            echo 'Before retrying, inspect the target state. A lost connection at COMMIT can leave the outcome unknown.' >&2
        fi
    fi
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ $(hostname -s) == db01 ]] || { echo "Expected hostname db01." >&2; exit 1; }
[[ -d $deploy_dir && -f $init ]]
[[ $(sha256sum "$init" | cut -d' ' -f1) == "$expected_sql" ]] || {
    echo 'init.sql does not match reviewed v1.12 (use an LF checkout).' >&2
    exit 1
}
if [[ $mode == --apply ]]; then
    exec 9>"$deploy_dir/.schema-deploy.lock"
    flock -n 9 || { echo 'Another schema deployment holds the lock.' >&2; exit 1; }
fi

[[ $("${docker[@]}" inspect --format '{{.State.Running}}' "$container") == true ]]
[[ $("${docker[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container") != unhealthy ]]
image_id=$("${docker[@]}" inspect --format '{{.Image}}' "$container")
"${docker[@]}" image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image_id" |
    grep -E "@$digest$" >/dev/null || { echo 'Running image digest differs or cannot be verified.' >&2; exit 1; }
printf 'Running image ID %s, verified registry digest %s\n' "$image_id" "$digest"
df -h -- "$deploy_dir"
free -h
if command -v ufw >/dev/null; then ufw status; fi
psql_db "$database" -P pager=off <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT current_database(), current_user, version();
SHOW shared_preload_libraries;
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name IN ('vector','pgcrypto','pg_textsearch','timescaledb') ORDER BY 1;
SELECT schemaname, tablename, tableowner FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%' ORDER BY 1,2;
SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname IN ('postgres','rag_app','rag_pipeline','rag_admin','rag_monitor') ORDER BY 1;
SELECT rule_number, type, database, user_name, address, auth_method, error IS NOT NULL AS parse_error
FROM pg_hba_file_rules ORDER BY rule_number NULLS LAST;
SELECT (SELECT count(*) FROM pg_stat_activity
        WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') AS other_clients;
ROLLBACK;
SQL
# Reused inside the deployment transaction to catch drift after the backup.
gate_sql=$(cat <<'SQL'
DO $gate$
BEGIN
    IF current_database() <> 'rag_dev' OR current_setting('server_version_num')::int / 10000 <> 17 THEN
        RAISE EXCEPTION 'Unexpected database or PostgreSQL major version';
    END IF;
    IF NOT regexp_split_to_array(current_setting('shared_preload_libraries'), '\s*,\s*')
           @> ARRAY['timescaledb','pg_textsearch'] THEN
        RAISE EXCEPTION 'Required libraries are not preloaded';
    END IF;
    IF EXISTS (
        SELECT FROM (VALUES ('vector','0.8.7'),('pg_textsearch','1.5.1'),
                           ('timescaledb','2.32.1'),('pgcrypto','1.3')) AS wanted(name,ver)
        LEFT JOIN pg_available_extensions e USING (name)
        WHERE e.name IS NULL OR coalesce(e.installed_version, e.default_version) <> wanted.ver
    ) THEN RAISE EXCEPTION 'An extension is unavailable or has an unexpected version'; END IF;
    IF EXISTS (
        SELECT FROM pg_namespace
        WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')
          AND NOT EXISTS (SELECT FROM pg_extension WHERE extnamespace=pg_namespace.oid)
          AND nspname !~ '^_timescaledb_'
          AND nspname NOT IN ('timescaledb_information','timescaledb_experimental')
    ) THEN RAISE EXCEPTION 'Unexpected application schema; review migration separately'; END IF;
    IF EXISTS (
        SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f','c')
          AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass
                          AND d.objid=c.oid AND d.deptype='e')
    ) OR EXISTS (
        SELECT FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
          AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                          AND d.objid=p.oid AND d.deptype='e')
    ) THEN RAISE EXCEPTION 'Existing application objects; v1.12 requires a migration review'; END IF;
    IF (SELECT count(*) FROM pg_roles
        WHERE rolname IN ('rag_app','rag_pipeline','rag_admin','rag_monitor') AND rolcanlogin) <> 4 THEN
        RAISE EXCEPTION 'Expected login roles are missing';
    END IF;
    IF EXISTS (
        SELECT FROM pg_roles s CROSS JOIN pg_roles r
        WHERE s.rolname IN ('rag_app','rag_pipeline')
          AND (s.oid=r.oid OR pg_has_role(s.oid,r.oid,'SET') OR pg_has_role(s.oid,r.oid,'USAGE'))
          AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls
               OR r.rolname IN ('rag_admin','rag_monitor','pg_write_all_data','pg_database_owner')
               OR r.oid=(SELECT datdba FROM pg_database WHERE datname=current_database())
               OR has_schema_privilege(r.oid,'public','CREATE'))
    ) THEN RAISE EXCEPTION 'An app or pipeline role can reach administrative or schema creation privileges'; END IF;
    IF EXISTS (SELECT FROM pg_hba_file_rules WHERE error IS NOT NULL) THEN
        RAISE EXCEPTION 'HBA file contains a parse error';
    END IF;
    IF EXISTS (SELECT FROM pg_stat_activity
               WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend') THEN
        RAISE EXCEPTION 'Active DB client connections; use an approved maintenance window';
    END IF;
END
$gate$;
SQL
)
printf 'BEGIN READ ONLY;\nSET LOCAL statement_timeout = %s;\n%s\nROLLBACK;\n' "'30s'" "$gate_sql" |
    psql_db "$database" -q
if [[ $mode == --check ]]; then
    echo 'Read-only preflight passed. No backup or database writes performed.'
    exit 0
fi

phase=backup
backup=$(mktemp -d "$deploy_dir/schema-backup.XXXXXXXX")
archive="$backup/rag_dev.dump"
db_exec pg_dump --no-password --username postgres --format=custom "$database" >"$archive" 2>"$backup/backup.log"
test -s "$archive"
(cd "$backup" && sha256sum rag_dev.dump >rag_dev.dump.sha256 && sha256sum -c rag_dev.dump.sha256)
db_exec pg_restore --list <"$archive" >"$backup/archive.list" 2>>"$backup/backup.log"

phase=restore
candidate="rag_schema_restore_$(date +%s)_$$_$RANDOM"
db_exec createdb --no-password --username postgres --template template0 "$candidate" >"$backup/restore.log" 2>&1
scratch=$candidate
db_exec pg_restore --exit-on-error --single-transaction --no-password \
    --username postgres --dbname "$scratch" <"$archive" >>"$backup/restore.log" 2>&1
psql_db "$scratch" -At -c 'SELECT extname,extversion FROM pg_extension ORDER BY 1' >"$backup/restore.extensions"
psql_db "$database" -At -c 'SELECT extname,extversion FROM pg_extension ORDER BY 1' >"$backup/original.extensions"
cmp -s "$backup/original.extensions" "$backup/restore.extensions" || {
    echo 'Restored extension inventory differs. Stopping before deployment.' >&2; exit 1;
}
db_exec dropdb --no-password --username postgres "$scratch" >>"$backup/restore.log" 2>&1
scratch=''
printf 'Backup restored successfully in temporary DB. Archive: %s\n' "$archive"

phase=apply
# The reviewed file has one outer BEGIN/COMMIT pair. Own the boundary here so
# schema, grants and assertions either commit together or roll back together.
{
    printf 'BEGIN;\nSET LOCAL lock_timeout = %s;\nSET LOCAL statement_timeout = %s;\n' "'5s'" "'120s'"
    printf 'SET LOCAL search_path = public, pg_catalog;\n%s\n' "$gate_sql"
    sed '/^BEGIN;$/d; /^COMMIT;$/d' "$init"
    cat <<'SCHEMA_POLICY'
-- Executed inside the schema transaction by deploy-schema.sh.
GRANT CONNECT ON DATABASE rag_dev TO rag_app, rag_pipeline;
GRANT USAGE ON SCHEMA public TO rag_app, rag_pipeline;
GRANT SELECT ON departments, ontology_concepts, documents, document_versions, chunks TO rag_app;
GRANT INSERT (action, actor_user_id, target_user_id, detail_json, ip_address)
    ON auth_audit_logs TO rag_app;
GRANT SELECT ON ontology_concepts, documents, document_versions, chunks TO rag_pipeline;
GRANT INSERT ON documents, document_versions, chunks TO rag_pipeline;
GRANT UPDATE (current_version_id) ON documents TO rag_pipeline;

DO $verify$
BEGIN
    IF (SELECT count(*) FROM public.departments) <> 10
        OR (SELECT count(*) FROM public.ontology_concepts) <> 12
        OR EXISTS (SELECT FROM public.users) THEN RAISE EXCEPTION 'Unexpected seed counts'; END IF;
    IF EXISTS (
        SELECT FROM (VALUES ('departments'),('users'),('auth_audit_logs'),('documents'),
                             ('document_versions'),('chunks'),('ontology_concepts'),('answer_feedbacks')) AS wanted(name)
        WHERE to_regclass('public.' || name) IS NULL
    ) THEN RAISE EXCEPTION 'Missing core table'; END IF;
    IF (SELECT count(*) FROM pg_extension WHERE extname IN ('pgcrypto','vector','pg_textsearch') AND extversion IN ('1.3','0.8.7','1.5.1')) <> 3
        OR (SELECT format_type(atttypid,atttypmod) FROM pg_attribute
            WHERE attrelid='public.chunks'::regclass AND attname='embedding') IS DISTINCT FROM 'halfvec(1024)'
        OR NOT EXISTS (SELECT FROM pg_indexes WHERE schemaname='public' AND indexname='idx_chunks_embedding_hnsw'
                       AND indexdef LIKE '%USING hnsw (embedding halfvec_cosine_ops)%')
        OR NOT EXISTS (SELECT FROM pg_indexes WHERE schemaname='public' AND indexname='idx_chunks_bm25'
                       AND indexdef LIKE '%USING bm25 (tsv_to_text(tsv))%') THEN
        RAISE EXCEPTION 'Required extensions or retrieval column/indexes are missing';
    END IF;
    IF EXISTS (
        SELECT FROM (VALUES ('auth_audit_logs'),('answer_feedbacks')) AS wanted(name)
        WHERE (SELECT count(*) FROM pg_partition_tree(('public.'||name)::regclass) WHERE isleaf) <> 4
    ) OR (SELECT count(*) FROM pg_trigger
          WHERE tgrelid IN ('public.users'::regclass,'public.documents'::regclass)
          AND tgname IN ('trg_users_updated_at','trg_documents_updated_at') AND tgenabled='O') <> 2 THEN
        RAISE EXCEPTION 'Partition or trigger check failed';
    END IF;
    -- Check every table/partition, under the login or any reachable SET ROLE identity.
    IF EXISTS (
        SELECT FROM pg_roles s CROSS JOIN pg_roles r CROSS JOIN pg_class c
        WHERE s.rolname IN ('rag_app','rag_pipeline')
          AND (s.oid=r.oid OR pg_has_role(s.oid,r.oid,'SET'))
          AND c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
          AND (pg_has_role(r.oid,c.relowner,'USAGE') OR
               has_table_privilege(r.oid,c.oid,'DELETE,TRUNCATE,TRIGGER'))
    ) THEN RAISE EXCEPTION 'Service role can own, delete, truncate or add triggers to a table'; END IF;

    IF EXISTS (
        SELECT FROM pg_roles s CROSS JOIN pg_roles r CROSS JOIN pg_class c
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE s.rolname IN ('rag_app','rag_pipeline')
          AND (s.oid=r.oid OR pg_has_role(s.oid,r.oid,'SET'))
          AND c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
          AND (
            (has_column_privilege(r.oid,c.oid,a.attnum,'SELECT') AND NOT (
              c.relname IN ('ontology_concepts','documents','document_versions','chunks') OR
              (s.rolname='rag_app' AND c.relname='departments')
            )) OR
            (has_column_privilege(r.oid,c.oid,a.attnum,'INSERT') AND NOT (
              (s.rolname='rag_pipeline' AND c.relname IN ('documents','document_versions','chunks')) OR
              (s.rolname='rag_app' AND c.relname='auth_audit_logs' AND
               a.attname IN ('action','actor_user_id','target_user_id','detail_json','ip_address'))
            )) OR
            (has_column_privilege(r.oid,c.oid,a.attnum,'UPDATE') AND NOT (
              s.rolname='rag_pipeline' AND c.relname='documents' AND a.attname='current_version_id'
            )) OR has_column_privilege(r.oid,c.oid,a.attnum,'REFERENCES')
          )
    ) THEN RAISE EXCEPTION 'An inherited, PUBLIC or default grant exceeds the service policy'; END IF;
END
$verify$;

-- Exercise the actual identities and operations before committing.
SET LOCAL ROLE rag_app;
SELECT dept_code FROM departments LIMIT 1;
SELECT concept_id FROM ontology_concepts LIMIT 1;
SELECT d.doc_id FROM documents d JOIN document_versions v USING (doc_id)
JOIN chunks c ON c.version_id=v.version_id LIMIT 1;
EXPLAIN (COSTS OFF) INSERT INTO auth_audit_logs(action,actor_user_id,target_user_id,detail_json,ip_address)
VALUES ('LOGIN',NULL,NULL,'{}',NULL);
RESET ROLE;

SET LOCAL ROLE rag_pipeline;
SELECT concept_id FROM ontology_concepts LIMIT 1;
EXPLAIN (COSTS OFF) INSERT INTO documents(doc_id,title,doc_type,dept_owner)
VALUES ('check','check','official_policy','COMMON');
EXPLAIN (COSTS OFF) INSERT INTO document_versions(doc_id,version_tag,raw_markdown,content_hash)
VALUES ('check','v1','check','check') RETURNING version_id;
EXPLAIN (COSTS OFF) INSERT INTO chunks(chunk_id,doc_id,version_id,chunk_index,section_path,content,tsv,embedding)
SELECT 'check', 'check', NULL::uuid, 1, 'check', 'check', to_tsvector('simple','check'),
       array_fill(0.0::real,ARRAY[1024])::halfvec;
EXPLAIN (COSTS OFF) UPDATE documents SET current_version_id=NULL WHERE doc_id='check';
RESET ROLE;
SCHEMA_POLICY
    printf '\nCOMMIT;\n'
} | psql_db "$database" -q >"$backup/apply.log" 2>&1
phase=postcommit
psql_db "$database" -At -c "SELECT to_regclass('public.chunks') IS NOT NULL AND to_regclass('public.idx_chunks_bm25') IS NOT NULL" |
    grep -Fxq t
printf 'Schema v1.12 committed and checked. Backup: %s\n' "$backup"
