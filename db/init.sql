-- =============================================================================
-- [MLO_01_FINAL] 정보지식플랫폼 PostgreSQL 17+ 통합 초기화 스크립트 (init.sql v1.12)
-- 작성/관리: 역할 1 (PM · DBA 메인) / 관련 티켓: W1-R1-04A, W1-R1-04B, W1-R3-02B
-- 아키텍처 기준: 04_레이크하우스_및_DB_ERD_설계서.md (v1.12 · 100% 1:N 무조인 · HALFVEC 1024 · PG 17+)
-- 특징:
--   1. N:M 조인 테이블(document_acl, ontology_relations) 완전 제거 -> 100% 1:N 구조
--   2. 4주차 코어 7개 테이블 + 8주차 확장 테이블(answer_feedbacks) 및 확장 컬럼 5종 선반영
--   3. updated_at 자동 갱신 트리거 내장 (퇴사자/권한변경 2초 내 즉시 차단 Q6 지원)
--   4. 10개 부서(COMMON + 9대 부서) OJT 추천 질문 70선·업종 라벨·14계정 및 12대 온톨로지 개념 시드 포함
-- =============================================================================

BEGIN;

-- 0. 필수 확장 모듈 활성화 (pgvector: 벡터 검색 / pgcrypto: UUID 및 표준 bcrypt 해시 생성 / pg_textsearch: BM25 검색)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;

-- 0-1. updated_at 자동 갱신 트리거 함수 정의
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 1. departments — 전사 공통 + 9대 부서 마스터 테이블
-- =============================================================================
CREATE TABLE IF NOT EXISTS departments (
    dept_code VARCHAR(32) PRIMARY KEY,
    default_name VARCHAR(64) NOT NULL,
    layer_group VARCHAR(32) NOT NULL CHECK (layer_group IN ('common', 'admin', 'business')),
    ad_group_name VARCHAR(128), -- [선택 범위] Active Directory 연동 시 매핑할 AD 그룹명
    is_active BOOLEAN NOT NULL DEFAULT TRUE, -- 어드민 [✓] 부서 활성 체크박스 연동 (폐지 부서는 FALSE)
    ojt_questions JSONB NOT NULL DEFAULT '[]'::jsonb, -- 부서별 신입 OJT 추천 질문 배열
    pack_labels JSONB NOT NULL DEFAULT '{}'::jsonb -- [확장 컬럼 선반영] 업종 패키지별 사업부서 표시 라벨 매핑
);

-- =============================================================================
-- 2. users — 회원 및 권한(RBAC · 어드민 체크박스 승인 · 선택형 AD 겸용) 테이블
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(128) UNIQUE NOT NULL,
    employee_no VARCHAR(50), -- 사번 식별자 (선택 입력)
    auth_provider VARCHAR(16) NOT NULL DEFAULT 'LOCAL' CHECK (auth_provider IN ('LOCAL', 'AD')),
    password_hash TEXT, -- LOCAL 모드는 필수(bcrypt), AD 모드는 NULL 허용
    external_ad_id VARCHAR(128), -- [선택 범위] AD sAMAccountName / UPN 식별자
    name VARCHAR(64) NOT NULL,
    requested_dept_code VARCHAR(32) REFERENCES departments(dept_code), -- 가입 시 희망 부서 (어드민 프리체크 표시 전용, ACL 참조 금지)
    dept_code VARCHAR(32) REFERENCES departments(dept_code), -- 어드민 체크박스 승인 시 확정된 주부서 (실제 ACL 기준)
    extra_depts TEXT[] NOT NULL DEFAULT '{}' CHECK (
        extra_depts <@ ARRAY['COMMON','HR','FIN','LGL','OPS','CX','PLN','ENG','INF','SLS']::text[]
    ), -- [확장 컬럼 선반영] 어드민 복수 부서 체크 시 자동 분리 저장되는 겸직 부서 배열
    role VARCHAR(32) NOT NULL DEFAULT 'EMPLOYEE' CHECK (role IN ('ADMIN', 'DEPT_MANAGER', 'EMPLOYEE', 'NEW_HIRE')),
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'REVISION_REQ', 'REJECTED', 'ACTIVE', 'INACTIVE')), -- 미승인(PENDING/REVISION_REQ) 시 본인 신청서 수정 및 PUBLIC 문서(회사소개서/팜플렛)만 열람 허용
    approved_by UUID REFERENCES users(user_id),
    approved_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(), -- 상태/부서/역할 변경 시각 (인증 미들웨어 2초 내 즉시 차단 검증용)
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_dept_status ON users (dept_code, status);
CREATE INDEX IF NOT EXISTS idx_users_extra_depts_gin ON users USING gin (extra_depts);

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 3. auth_audit_logs — 보안 · 인증 · 권한 · 문서검수 감사 로그 테이블 (users 1:N, 분기 파티션)
-- =============================================================================
CREATE TABLE IF NOT EXISTS auth_audit_logs (
    log_id UUID DEFAULT gen_random_uuid(),
    actor_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    target_user_id UUID,
    action VARCHAR(32) NOT NULL, -- SIGNUP | APPROVE | DEACTIVATE | REACTIVATE | ROLE_DEPT_CHANGE | LOGIN | ACL_DENIED | SIGNUP_REV_REQ | SIGNUP_REJECT | USER_CREATE | USER_IMPORT | PW_CHANGE | PW_RESET | DOC_APPROVE | DOC_REJECT | DOC_UNPUBLISH | DOC_VERIFY | DOC_EXTEND | DOC_EXPIRE | DOC_REACTIVATE | BUILDER_PUBLISH | PACK_SWITCH
    detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip_address VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (log_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS auth_audit_logs_2026_q4 PARTITION OF auth_audit_logs
    FOR VALUES FROM ('2026-10-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS auth_audit_logs_2027_q1 PARTITION OF auth_audit_logs
    FOR VALUES FROM ('2027-01-01') TO ('2027-04-01');
CREATE TABLE IF NOT EXISTS auth_audit_logs_2027_q2 PARTITION OF auth_audit_logs
    FOR VALUES FROM ('2027-04-01') TO ('2027-07-01');
CREATE TABLE IF NOT EXISTS auth_audit_logs_default PARTITION OF auth_audit_logs DEFAULT;

CREATE INDEX IF NOT EXISTS idx_audit_logs_action_created ON auth_audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON auth_audit_logs (actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target ON auth_audit_logs (target_user_id, action, created_at DESC);

-- =============================================================================
-- 4. documents — 문서 마스터 · 무조인 부서 권한(allowed_depts) · 사전 컴파일 그래프 내장 테이블
-- =============================================================================
CREATE TABLE IF NOT EXISTS documents (
    doc_id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    doc_type VARCHAR(32) NOT NULL CHECK (doc_type IN ('official_policy', 'standard_template', 'ojt_guide', 'benchmark_reference')),
    source_tier SMALLINT NOT NULL DEFAULT 1 CHECK (source_tier IN (1, 2)), -- 1: 자사 공식 사규(최우선), 2: 공공 표준양식/벤치마크
    knowledge_pack VARCHAR(32) NOT NULL DEFAULT 'it_service' CHECK (knowledge_pack IN ('base', 'it_service', 'ecommerce', 'benchmark')),
    dept_owner VARCHAR(32) NOT NULL REFERENCES departments(dept_code),
    allowed_depts TEXT[] NOT NULL DEFAULT '{ALL_EMP}' CHECK (
        cardinality(allowed_depts) >= 1
        AND allowed_depts <@ ARRAY['PUBLIC','ALL_EMP','COMMON','HR','FIN','LGL','OPS','CX','PLN','ENG','INF','SLS']::text[]
        AND NOT ('PUBLIC' = ANY(allowed_depts) AND cardinality(allowed_depts) > 1)
        AND NOT ('ALL_EMP' = ANY(allowed_depts) AND cardinality(allowed_depts) > 1)
    ), -- 3단 공개 계층 단일 컬럼 통합(별도 is_open 컬럼 불필요): 1계층 PUBLIC(비로그인/미승인 공개: 회사소개서·팜플렛) | 2계층 ALL_EMP(승인 전직원 공개) | 3계층 개별 부서코드 (상호 배타 강제)
    classification_method VARCHAR(32) NOT NULL DEFAULT 'manual_frontmatter' CHECK (classification_method IN ('manual_frontmatter', 'auto_dict_rule')),
    needs_admin_review BOOLEAN NOT NULL DEFAULT FALSE,
    is_discoverable BOOLEAN NOT NULL DEFAULT FALSE, -- 권한 외 사용자에게 제목/주관부서 존재 안내 노출 여부 (본문/청크는 무조건 차단)
    sync_quarter VARCHAR(16) NOT NULL DEFAULT '2026-Q4',
    last_verified_at TIMESTAMP, -- [회신 10차] 소관 부서 담당자(DEPT_MANAGER) 분기 유효 점검 확인 시각
    current_version_id UUID, -- 현행 active 버전 포인터 (폐기/급히내리기/초회검수대기 시 NULL)
    ontology_graph_meta JSONB NOT NULL DEFAULT '{}'::jsonb, -- [확장 컬럼 선반영] Delta Silver에서 양방향 사전 컴파일된 prev_steps, next_steps, required_forms, conflicts 그래프 뷰
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_allowed_depts_gin ON documents USING gin (allowed_depts);
CREATE INDEX IF NOT EXISTS idx_documents_pack_owner ON documents (knowledge_pack, dept_owner);
CREATE INDEX IF NOT EXISTS idx_documents_onto_graph_gin ON documents USING gin (ontology_graph_meta);

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON documents
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 5. document_versions — 분기별 문서 개정 버전 및 원문 불변 스냅샷 테이블 (documents 1:N)
-- =============================================================================
CREATE TABLE IF NOT EXISTS document_versions (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id VARCHAR(64) NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    version_tag VARCHAR(32) NOT NULL,
    sync_quarter VARCHAR(16) NOT NULL DEFAULT '2026-Q4',
    source_origin VARCHAR(32) NOT NULL DEFAULT 'pipeline' CHECK (source_origin IN ('pipeline', 'builder')), -- [확장 컬럼 선반영]
    snapshot_uri TEXT, -- [확장 컬럼 선반영] 사규 빌더 즉시 발행 시 Object Storage 백업 경로
    raw_markdown TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    version_status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (version_status IN ('active', 'pending_review', 'archived')),
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    source_org VARCHAR(128),
    source_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (doc_id, version_tag)
);

CREATE INDEX IF NOT EXISTS idx_doc_versions_doc_status ON document_versions (doc_id, version_status);

-- =============================================================================
-- 6. chunks — 하이브리드 검색용 문단 조각 테이블 (documents/document_versions 1:N)
--    [반영] 2026-11-20 한국어 임베딩 후보(KURE-v3/BGE-M3 등) 1,024차원 기준 HALFVEC(1024) 적용
-- =============================================================================
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id VARCHAR(128) PRIMARY KEY, -- {doc_id}::{version_tag}::c{NNN}
    doc_id VARCHAR(64) NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    section_path TEXT NOT NULL,
    content TEXT NOT NULL,
    tsv TSVECTOR NOT NULL, -- 'simple' 설정 Kiwi 토큰 + 위치정보 유지 (BM25 tsv_to_text 식 인덱스 공용 컬럼)
    embedding HALFVEC(1024) NOT NULL,
    concept_ids TEXT[] NOT NULL DEFAULT '{}',
    applied_dict_version VARCHAR(32) NOT NULL DEFAULT 'v1.0'
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_version ON chunks (doc_id, version_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_concepts_gin ON chunks USING gin (concept_ids);

CREATE OR REPLACE FUNCTION tsv_to_text(t tsvector) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT coalesce(string_agg(u.lexeme, ' ' ORDER BY p), '')
  FROM unnest(t) AS u, unnest(u.positions) AS p
$$;

CREATE INDEX IF NOT EXISTS idx_chunks_bm25 ON chunks USING bm25 (tsv_to_text(tsv))
  WITH (text_config = 'simple', k1 = 1.2, b = 0.75);

-- =============================================================================
-- 7. ontology_concepts — 온톨로지 개념 · 동의어 · 자동 분류 사전 테이블 (departments 1:N)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ontology_concepts (
    concept_id VARCHAR(64) PRIMARY KEY,
    dict_version VARCHAR(32) NOT NULL DEFAULT 'v1.0',
    canonical_term VARCHAR(128) NOT NULL,
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    term_type VARCHAR(32) NOT NULL DEFAULT 'jargon' CHECK (term_type IN ('policy', 'form', 'process', 'jargon')),
    dept_owner VARCHAR(32) REFERENCES departments(dept_code),
    parent_concept_id VARCHAR(64) REFERENCES ontology_concepts(concept_id),
    related_doc_ids TEXT[] NOT NULL DEFAULT '{}',
    description TEXT NOT NULL,
    knowledge_pack VARCHAR(32) NOT NULL DEFAULT 'base' CHECK (knowledge_pack IN ('base', 'it_service', 'ecommerce', 'benchmark'))
);

CREATE INDEX IF NOT EXISTS idx_onto_synonyms_gin ON ontology_concepts USING gin (synonyms);
CREATE INDEX IF NOT EXISTS idx_onto_canonical_term ON ontology_concepts (canonical_term);

-- =============================================================================
-- 8. answer_feedbacks — 답변 좋아요 및 오답·개정 신고 검수 큐 테이블 (users 1:N · 분기 파티션)
-- =============================================================================
CREATE TABLE IF NOT EXISTS answer_feedbacks (
    feedback_id UUID DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    dept_code VARCHAR(32) REFERENCES departments(dept_code),
    knowledge_pack VARCHAR(32) NOT NULL DEFAULT 'it_service',
    query_text TEXT NOT NULL,
    answer_snapshot TEXT,
    cited_chunk_ids TEXT[] NOT NULL DEFAULT '{}',
    feedback_type VARCHAR(32) NOT NULL CHECK (feedback_type IN ('like', 'error_report', 'revision_request')),
    comment TEXT,
    review_status VARCHAR(32) NOT NULL DEFAULT 'open' CHECK (review_status IN ('open', 'in_review', 'resolved', 'dismissed')),
    resolved_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
    resolution_note TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP,
    PRIMARY KEY (feedback_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS answer_feedbacks_2026_q4 PARTITION OF answer_feedbacks
    FOR VALUES FROM ('2026-10-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS answer_feedbacks_2027_q1 PARTITION OF answer_feedbacks
    FOR VALUES FROM ('2027-01-01') TO ('2027-04-01');
CREATE TABLE IF NOT EXISTS answer_feedbacks_2027_q2 PARTITION OF answer_feedbacks
    FOR VALUES FROM ('2027-04-01') TO ('2027-07-01');
CREATE TABLE IF NOT EXISTS answer_feedbacks_default PARTITION OF answer_feedbacks DEFAULT;

CREATE INDEX IF NOT EXISTS idx_feedbacks_status_created ON answer_feedbacks (review_status, created_at DESC);

-- =============================================================================
-- [SEED DATA 1] 10개 부서 마스터 (전사 공통 COMMON + 행정 5대 부서 + 사업 4대 부서)
--               부서별 신입 OJT 대표 추천 질문(ojt_questions) 및 업종 라벨(pack_labels) 포함
-- =============================================================================
INSERT INTO departments (dept_code, default_name, layer_group, ad_group_name, is_active, ojt_questions, pack_labels)
VALUES
(
    'COMMON', '전사 공통', 'common', 'AD_GRP_COMMON', TRUE,
    '["입사 첫날 그룹웨어·사내 메신저 로그인과 VPN·2단계 인증(2FA)은 어떤 순서로 설정하나요?", "신입사원이 연차·반차·병가·경조사 휴가를 신청하려면 그룹웨어 어느 메뉴에서 며칠 전까지 올려야 하나요?", "월급날은 매월 며칠이고 수습 기간 중에도 급여가 100% 지급되나요? 급여명세서는 어디서 보나요?", "점심·야근 식대 지원 한도는 얼마이고 법인카드나 개인카드 영수증은 매월 언제까지 정산하나요?", "시차출퇴근 코어타임(11:00~15:00)과 재택근무 신청 조건은 어떻게 되나요?", "회사에서 자주 쓰는 ''품의서'', ''지출결의서(지결)'', ''업무협조전''은 각각 언제 작성하고 어떻게 연결되나요?", "전자결재를 올릴 때 금액이나 계약 중요도에 따라 팀장·본부장·대표이사 전결권은 어떻게 나뉘나요?", "타 부서에 공식 업무 요청을 보낼 때 표준 업무협조전에는 어떤 항목을 빠짐없이 적어야 하나요?"]'::jsonb,
    '{"base": "전사 공통", "it_service": "전사 공통", "ecommerce": "전사 공통"}'::jsonb
),
(
    'HR', '인사·노무', 'admin', 'AD_GRP_HR', TRUE,
    '["수습 3개월 기간 중에도 연차·반차·반반차를 사용할 수 있나요? 당일 연차 사용도 가능한가요?", "몸이 아파서 출근이 어려울 때 일반 병가는 연간 며칠까지 쓸 수 있고 유급인지 무급인지 알려주세요.", "병가로 3일 이상 쉬거나 당일 아침 급하게 병가를 쓸 때 진단서 제출 기준과 보고 절차는 어떻게 되나요?", "본인·가족 경조사 유급휴가 일수와 청첩장·부고장 증빙, 경조금 신청 방법은 무엇인가요?", "예비군·민방위 훈련이나 국가 건강검진, 법원 소환 시 공가 신청 방법과 제출 서류는 무엇인가요?", "신입사원 입사 첫 주에 인사팀에 제출해야 하는 필수 서류(건강검진·보안서약 등) 체크리스트를 알려주세요.", "시차출퇴근 신청 방법과 코어타임, 그리고 재택근무 시 근태 체크 및 화상회의 준수 수칙은 무엇인가요?", "직장 내 괴롭힘·성희롱 등 고충이 발생했을 때 익명 신고 경로와 인사팀의 조사·보호 절차는 어떻게 되나요?", "표준 휴가·병가·경조사·재택근무 신청서 작성 시 사유, 대직자 지정, 증빙 첨부 필수 항목은 무엇인가요?", "부서원이 연봉 테이블이나 인사평가 등급 기준을 물어볼 때 인사팀 문서 접근 권한과 안내 원칙은 무엇인가요?"]'::jsonb,
    '{"base": "인사·노무", "it_service": "인사·노무", "ecommerce": "인사·노무"}'::jsonb
),
(
    'FIN', '재무·회계·세무', 'admin', 'AD_GRP_FIN', TRUE,
    '["야근 식대나 팀 점심·회식비를 사용할 때 1인당 지원 한도와 영수증에 꼭 적어야 할 내용은 무엇인가요?", "법인카드 사용 후 매월 언제까지 정산해야 하고, 주말·심야·유흥업종 등 사용이 금지되는 기준은 무엇인가요?", "외근 교통비나 국내 출장 시 자차 유류비·KTX·숙박비·일비 상한 기준과 출장 후 정산 기한은 언제까지인가요?", "개인 카드로 업무 비용을 먼저 결제했을 때 환급받을 수 있나요? 지출결의서로 처리하는 조건을 알려주세요.", "지출결의서(지결) 작성 시 반드시 첨부해야 하는 적격 증빙 4종과 매주 마감·송금 일정은 어떻게 되나요?", "매주 화요일 마감 이후 거래처 긴급 대금을 바로 지급해야 할 때 긴급 결제 예외 품의 절차는 어떻게 되나요?", "거래처에 전자세금계산서를 발행 요청하거나 수취할 때 마감일과 필수 확인 사업자 정보는 무엇인가요?", "법인카드·여비 지출결의서 표준 양식에서 비용 계정과목 선택 기준과 반려 빈도가 높은 누락 항목은 무엇인가요?"]'::jsonb,
    '{"base": "재무·회계·세무", "it_service": "재무·회계·세무", "ecommerce": "재무·회계·세무"}'::jsonb
),
(
    'LGL', '법무·컴플라이언스', 'admin', 'AD_GRP_LGL', TRUE,
    '["타 부서에서 계약서 검토를 요청하기 전에 실무자가 법무팀에 반드시 먼저 물어보거나 구두 합의를 피해야 할 원칙은 무엇인가요?", "법무팀 표준 비밀유지계약서(NDA) 양식을 사용할 때와 상대방 NDA를 받았을 때 검토 절차는 어떻게 다른가요?", "계약서 사전 검토 요청 시 법무팀에 반드시 첨부해야 할 필수 5종 서류와 표준·비표준 검토 기간(SLA)은 며칠인가요?", "비밀유지계약서(NDA) 요청서에 꼭 채워야 하는 별첨 7대 필수 기재 항목(비밀정보 범위·유효기간 등)은 무엇인가요?", "계약서 법무 검토 완료 후 법인인감 날인 신청 절차와 전자서명·종이계약서 사용대장 기록 기준은 무엇인가요?", "제휴사·외주사에 고객 개인정보를 제공하거나 위탁할 때 계약서에 꼭 넣어야 하는 개인정보 보호 조항은 무엇인가요?", "개발·기획 부서가 상용 서비스에 오픈소스(OSS)나 외부 SDK를 넣으려 할 때 법무팀 라이선스 검증 기준은 무엇인가요?"]'::jsonb,
    '{"base": "법무·컴플라이언스", "it_service": "법무·컴플라이언스", "ecommerce": "법무·컴플라이언스"}'::jsonb
),
(
    'OPS', '경영운영·총무', 'admin', 'AD_GRP_OPS', TRUE,
    '["사내 공용 비품·도서 구매나 사무실 시설물 수리가 필요할 때 신청 절차와 승인 한도는 어떻게 되나요?", "사내 보안 문서 반출 금지, 출입 보안, 윤리강령 위반이나 시설 안전사고 목격 시 즉시 보고 절차는 어떻게 되나요?", "일반 비품이나 외주 용역 구매품의서를 올릴 때 금액 기준별(100만·1,000만 원 등) 비교견적서 첨부 규칙은 무엇인가요?", "구매품의서 표준 양식에 꼭 적어야 하는 필수 항목(품목·예산계정·검수예정일 등)과 작성 요령을 알려주세요.", "외주·소프트웨어 도입 시 법무(LGL) 검토, 총무(OPS) 구매품의, 재무(FIN) 지출결의는 어떤 순서로 연결하나요?", "타 부서에 시설·자산·계약 협조를 공식 요청할 때 쓰는 표준 업무협조전(STD-OPS-021) 필수 항목은 무엇인가요?", "과거 구버전(v0.9) 구매 규정 대비 현행 규정에서 달라진 점은 무엇이며, 업무 중 사규 오기재를 발견하면 어떻게 정정 요청하나요?"]'::jsonb,
    '{"base": "경영운영·총무", "it_service": "경영운영·총무", "ecommerce": "경영운영·총무"}'::jsonb
),
(
    'CX', '고객운영·CS', 'admin', 'AD_GRP_CX', TRUE,
    '["고객 문의와 클레임 등급(Tier 1~3)은 어떤 기준으로 나뉘고, 등급별 1차 응답 및 완료 목표 시간(SLA)은 어떻게 되나요?", "상담 중 고객 개인정보(연락처·결제내역·계정 로그)를 조회할 때 신입이 꼭 지켜야 할 최소 조회와 마스킹 원칙은 무엇인가요?", "CS 단독 처리가 어려운 버그·환불 분쟁·언론/법적 클레임을 개발(ENG)·법무(LGL)·재무(FIN) 팀으로 이관할 때 작성하는 요청서 양식 항목은 무엇인가요?", "고객 단순 변심 환불 vs 서비스 장애 보상금(크레딧·위약금 면제) 산정 기준과 재무팀 환불 정산 연동 절차는 어떻게 되나요?", "폭언·협박·내용증명 등 악성 민원이나 법적 조치 예고 고객이 접수됐을 때 응대 종료 및 법무(LGL)·인사 보고 절차는 무엇인가요?", "신규 기능이나 요금제 변경 배포 전(D-7) 기획팀(PLN)으로부터 CS 팀이 넘겨받아 확인해야 하는 사전 합의 체크리스트는 무엇인가요?"]'::jsonb,
    '{"base": "고객운영·CS", "it_service": "고객운영·CS", "ecommerce": "고객운영·CS"}'::jsonb
),
(
    'PLN', '기획·전략', 'business', 'AD_GRP_PLN', TRUE,
    '["신규 프로젝트 기획안(Pitch/PRD)을 작성할 때 반드시 포함해야 할 필수 구성 항목과 템플릿 기준은 무엇인가요?", "신규 기획이나 프로모션 예산을 편성할 때 비용 계정 구분과 재무(FIN)·총무(OPS)팀 사전 확인 사항은 무엇인가요?", "새로운 기능이나 요금제·서비스 정책을 변경할 때 법무(D-14)·보안(D-10)·CS(D-7) 팀에 각각 어떤 자료를 넘겨 사전 합의를 받나요?", "무료체험 자동 유료전환, 해지 방해, 숨은 비용 등 다크패턴 방지 체크리스트 중 기획안 확정 전 법무와 확인해야 할 핵심 기준은 무엇인가요?", "확정된 기획 정책 변경이나 일정을 개발(ENG)·CS(CX)·영업(SLS)에 공지할 때 협조전 작성 및 버전 관리 규칙은 무엇인가요?", "프로젝트 진행 중 외주 추가나 예산 초과가 예상될 때 경영운영(OPS)·재무(FIN)팀과 변경 품의를 진행하는 절차는 어떻게 되나요?"]'::jsonb,
    '{"base": "기획·전략", "it_service": "서비스기획·전략", "ecommerce": "상품기획·MD"}'::jsonb
),
(
    'ENG', '제작·개발', 'business', 'AD_GRP_ENG', TRUE,
    '["신입 개발자가 입사 첫 주에 세팅해야 할 로컬 개발환경·계정과 우리 팀의 Git 브랜치·PR 코드 리뷰 승인 규칙은 무엇인가요?", "개발 환경에서 시크릿 키·DB 비밀번호를 다룰 때 코드에 하드코딩하지 않고 안전하게 관리하는 보안 규칙은 무엇인가요?", "테스트 서버와 운영 서버에 코드를 배포할 때 필요한 사전 체크리스트와 배포 금지 시간대·롤백 기준은 어떻게 되나요?", "서비스 운영 장애(SEV-1~3)가 발생했을 때 CS(CX)·인프라(INF) 동시 공지 절차와 사후 회고서(Post-mortem) 작성 기한은 언제까지인가요?", "개발·장애 분석 중 운영 DB 조회 권한이나 방화벽 오픈이 필요할 때 인프라(INF)팀에 올리는 협조전 필수 기재 항목은 무엇인가요?", "신규 오픈소스(OSS) 라이브러리나 외부 AI API를 서비스에 도입할 때 법무(LGL) 및 보안(INF)팀 사전 승인 기준은 무엇인가요?"]'::jsonb,
    '{"base": "제작·개발", "it_service": "소프트웨어개발", "ecommerce": "플랫폼개발·소싱"}'::jsonb
),
(
    'INF', '인프라·보안', 'business', 'AD_GRP_INF', TRUE,
    '["사내 내부망·클라우드 콘솔 접속을 위한 VPN과 2단계 인증(2FA) 초기 설정 방법 및 기기 분실 시 초기화 절차는 무엇인가요?", "사내 시스템 이용 중 접근 권한 오류나 에러 코드(ERR-*)가 발생했을 때 화면 캡처를 활용해 해결 및 문의하는 절차는 어떻게 되나요?", "타 부서 개발자나 기획자가 운영 DB 접근 권한 또는 방화벽 오픈 협조전을 올릴 때 신청서에 반드시 기입해야 하는 항목은 무엇인가요?", "개인정보보호법(PIPA) 기준 DB·로그에 저장되는 주민번호·계좌·연락처의 마스킹 및 암호화 보관 기준은 어떻게 되나요?", "클라우드 계정·IAM 권한·운영 API 키 발급 신청 방법과 부서 이동·퇴사 시 권한 회수 체크리스트는 무엇인가요?", "신규 서비스나 외부 제휴 연동 오픈 전(D-10) 기획(PLN)·개발(ENG)팀과 함께 점검하는 보안 사전 합의 체크리스트는 무엇인가요?"]'::jsonb,
    '{"base": "인프라·보안", "it_service": "클라우드인프라·보안", "ecommerce": "물류인프라·SCM"}'::jsonb
),
(
    'SLS', '영업·마케팅', 'business', 'AD_GRP_SLS', TRUE,
    '["신규 B2B 고객사와 첫 계약을 진행할 때 거래처 등록에 꼭 받아야 할 서류와 재무팀(FIN) 전달 절차는 어떻게 되나요?", "고객사에 영업 제안서나 NDA를 발송하기 전 법무팀(LGL) 표준 양식 사용 기준과 영업팀 유의사항은 무엇인가요?", "고객사에 B2B 할인·무상 제공 특례를 제안할 때 전사 전결 규정(IT-COMMON-002)과 영업본부 특례(IT-SLS-025) 중 어느 승인선이 적용되나요?", "B2B 수주 계약부터 매출 세금계산서 발행까지 법무(LGL)·총무(OPS)·재무(FIN) 팀과의 협업 순서와 마감일은 어떻게 되나요?", "영업 활동 중 거래처 접대비나 마케팅 프로모션 비용을 법인카드로 쓸 때 1회·월간 한도와 증빙 요건은 무엇인가요?", "신규 기능 출시나 요금 프로모션을 고객에게 먼저 안내해도 되는지 기획(PLN)·CS(CX)팀과 사전 확인하는 기준은 무엇인가요?"]'::jsonb,
    '{"base": "영업·마케팅", "it_service": "B2B영업·마케팅", "ecommerce": "셀러영업·퍼포먼스마케팅"}'::jsonb
)
ON CONFLICT (dept_code) DO UPDATE
SET default_name = EXCLUDED.default_name,
    ojt_questions = EXCLUDED.ojt_questions,
    pack_labels = EXCLUDED.pack_labels;

-- =============================================================================
-- [SEED DATA 2] 시연·테스트 계정 14개와 그 감사 로그 시드 2줄은 공개 저장소에서 뺐습니다(로그인 정보 노출 방지).
--               시연 계정은 메인기획 배포본으로 따로 넣습니다.
-- =============================================================================

-- =============================================================================
-- [SEED DATA 3] 핵심 온톨로지 개념·동의어 사전 초기 시드 12선 (ontology_concepts)
--   [갱신] 실제 179건 doc_id 매핑 동기화 및 6차 #11 GQA-ACL 동의어(연봉↔급여) 보강
-- =============================================================================
INSERT INTO ontology_concepts (
    concept_id, dict_version, canonical_term, synonyms, term_type,
    dept_owner, parent_concept_id, related_doc_ids, description, knowledge_pack
)
VALUES
(
    'CON-COM-001', 'v1.0', '위임전결',
    ARRAY['전결', '전결권자', '결재한도', '결재권자', '전결규정'],
    'policy', 'COMMON', NULL, ARRAY['IT-COMMON-002'],
    '업무 중요도와 금액 규모에 따라 대표이사·본부장·팀장 등 최종 결재 승인 권한을 위임하여 정해둔 전사 기준입니다.',
    'base'
),
(
    'CON-OPS-001', 'v1.0', '품의서',
    ARRAY['품의', '구매품의', '기안서', '사전품의', '집행품의'],
    'form', 'OPS', 'CON-COM-001', ARRAY['IT-OPS-013', 'STD-OPS-021'],
    '비용을 지출하거나 계약을 맺기 전에 "이러한 목적과 예산으로 업무를 진행하겠습니다"라고 사전 승인을 받는 결재 문서입니다.',
    'base'
),
(
    'CON-FIN-001', 'v1.0', '지출결의서',
    ARRAY['지결', '지결서', '비용정산', '경비청구', '대금지급결의'],
    'form', 'FIN', 'CON-COM-001', ARRAY['IT-FIN-008', 'STD-FIN-022'],
    '사전 품의 또는 법인카드 사용 후 실제 거래처 계좌로 대금을 송금하거나 정산받기 위해 증빙(세금계산서·영수증)을 첨부해 올리는 최종 회계 결재 문서입니다.',
    'base'
),
(
    'CON-COM-002', 'v1.0', '협조전',
    ARRAY['업무협조전', '열람협조', '타부서협조', '권한협조전'],
    'process', 'COMMON', NULL, ARRAY['IT-COMMON-003', 'STD-OPS-021'],
    '타 부서의 열람 제한 문서 확인이나 서버·DB 접근 권한 부여 등 타 부서장의 승인이 필요한 업무를 공식 요청하는 절차입니다.',
    'base'
),
(
    'CON-LGL-001', 'v1.0', '비밀유지계약서',
    ARRAY['NDA', '기밀유지협약', '비밀유지협약서'],
    'form', 'LGL', NULL, ARRAY['STD-LGL-004', 'IT-LGL-011'],
    '외부 파트너사나 외주 업체와 업무 협의 시 사내 영업비밀 및 기술 자료가 외부로 유출되지 않도록 사전에 체결하는 법적 계약서입니다.',
    'base'
),
(
    'CON-LGL-002', 'v1.0', '계약서 사전검토',
    ARRAY['법무검토', '계약검토', '인감날인신청'],
    'process', 'LGL', NULL, ARRAY['IT-LGL-011', 'IT-LGL-012'],
    '외주·용역·구매 품의를 올리기 전 법무팀에 계약서 초안의 독소조항 여부와 법적 리스크를 먼저 확인받는 필수 선행 단계입니다.',
    'base'
),
(
    'CON-FIN-002', 'v1.0', '법인카드 정산',
    ARRAY['법카정산', '법카영수증', '법인카드마감', '야근식대'],
    'process', 'FIN', 'CON-FIN-001', ARRAY['IT-FIN-009', 'IT-FIN-010'],
    '당월 사용한 법인카드 내역에 대해 참석자·목적·적격 증빙을 첨부하여 익월 정해진 마감일(매월 10일)까지 지출결의서로 상신하는 절차입니다.',
    'base'
),
(
    'CON-HR-001', 'v1.0', '온보딩 체크리스트',
    ARRAY['신입OJT', '입사첫주체크리스트', '신입온보딩', '입사제출서류'],
    'process', 'HR', NULL, ARRAY['IT-HR-005', 'IT-PUB-002'],
    '신규 입사자가 입사 첫 주에 완료해야 할 근로계약서·비밀유지서약 서명, 채용검진 제출, VPN/2FA 설정, 급여·근태 기본 규정 숙지 목록입니다.',
    'base'
),
(
    'CON-HR-002', 'v1.0', '급여',
    ARRAY['연봉', '연봉밴드', '보수', '상여', '성과상여', '스톡옵션', '급여일', '인사평가', '평가등급'],
    'policy', 'HR', NULL, ARRAY['IT-HR-005', 'IT-HR-007'],
    '정기 급여일(매월 25일)·급여명세서 조회 기준 및 인사팀 전용 연봉·상여·스톡옵션·인사평가 내규를 포괄하는 보상 개념입니다.',
    'base'
),
(
    'CON-HR-003', 'v1.0', '근태·휴가',
    ARRAY['연차', '반차', '반반차', '병가', '진단서', '경조사', '공가', '예비군', '유연근무', '코어타임', '재택근무'],
    'policy', 'HR', NULL, ARRAY['IT-HR-006', 'STD-HR-023'],
    '수습 중 연차·반차·반반차, 일반 병가(3일 이상 진단서), 경조사 휴가, 예비군·건강검진 공가, 시차출퇴근 코어타임(11:00~15:00) 및 재택근무 규정입니다.',
    'base'
),
(
    'CON-ENG-001', 'v1.0', '운영 배포 승인',
    ARRAY['릴리즈승인', '핫픽스배포', '배포절차', '코드리뷰승인'],
    'process', 'ENG', NULL, ARRAY['IT-ENG-020', 'IT-ENG-021'],
    '개발 완료된 코드를 실제 서비스 환경(Production)에 반영하기 전 PR 코드 리뷰와 QA 검증, 배포 책임자 승인을 거치는 절차입니다.',
    'it_service'
),
(
    'CON-INF-001', 'v1.0', '방화벽 오픈 신청',
    ARRAY['포트오픈', 'DB접근권한', '서버접근신청', 'VPN발급'],
    'process', 'INF', 'CON-COM-002', ARRAY['IT-INF-022', 'IT-INF-023'],
    '운영 서버나 데이터베이스에 접속하기 위해 인프라·보안팀에 출발지/목적지 IP와 포트 허용을 요청하는 보안 협조 절차입니다.',
    'it_service'
)
ON CONFLICT (concept_id) DO NOTHING;

-- [회신 10차] 앱/파이프라인 DB 계정의 연쇄 삭제(ON DELETE CASCADE) 방지: DELETE 권한 회수 (폐기는 archived 상태로만 처리)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_app') THEN
        REVOKE DELETE ON documents, document_versions, users FROM rag_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_pipeline') THEN
        REVOKE DELETE ON documents, document_versions, users FROM rag_pipeline;
    END IF;
END $$;

COMMIT;
