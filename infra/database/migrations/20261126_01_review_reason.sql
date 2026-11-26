-- Migration: 20261126_01
-- Related: Issue #40 / PR #41
-- Target: PostgreSQL 17 / rag_dev

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE public.documents
    ADD COLUMN review_reason VARCHAR(32)
    CHECK (
        review_reason IN (
            'LOW_CONFIDENCE',
            'SENSITIVE_DEPT',
            'QUARTERLY_OVERDUE'
        )
    );

ALTER TABLE public.document_versions
    DROP CONSTRAINT document_versions_version_status_check,
    ADD CONSTRAINT document_versions_version_status_check
    CHECK (
        version_status IN (
            'active',
            'pending_review',
            'archived',
            'rejected'
        )
    );

COMMIT;
