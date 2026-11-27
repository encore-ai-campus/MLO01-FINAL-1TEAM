# Database Schema Migrations

## 20261126_01 - Review reason and rejected status

- Applied date: 2026-11-26 (KST)
- Target server: DB01
- Database: rag_dev
- PostgreSQL: 17.12
- Related issue: #40
- Related pull request: #41
- Status: Applied

### Changes

- Add documents.review_reason VARCHAR(32).
- Add rejected to document_versions.version_status.
- Preserve existing data and service account grants.

### Backup

- Custom-format pg_dump completed.
- SHA-256 checksum verified.
- Temporary database restore performed.
- Backup retained on DB01.

### Verification

- Migration transaction committed successfully.
- Schema verification performed.
- APP01 OpenAPI: HTTP 200.
- APP01 document search API: HTTP 200.

### Precautions

- This migration has already been applied to DB01.
- Do not execute it again on the same database.
- New databases use the updated db/init.sql.
- Review the target schema and create a verified backup
  before applying to another existing database.
