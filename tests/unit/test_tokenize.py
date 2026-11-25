import pytest

from rag_api.shared.tokenize import ConceptTerm, current_dict_version, load_dictionary, tokenize_ko, tsv_source

# 경계 계약 2.5 의 사전
DICT_25 = [ConceptTerm(t) for t in ["지결", "영수증처리", "계약서 사전검토", "반차", "비용정산", "e-러닝"]]


@pytest.fixture(autouse=True)
def dict_25():
    load_dictionary(DICT_25, "t-2.5")


# 경계 계약 2.5 표. 글자 하나까지 같아야 함
@pytest.mark.parametrize("text, expected", [
    ("외주 용역 대금은 검수 완료일로부터 30일 이내에 정산합니다.", "외주 용역 대금 검수 완료 30 이내 정산"),
    ("지결 올릴 때 영수증처리는 어떻게 하나요?", "지결 때 영수증처리"),
    ("계약서  사전검토를 법무팀에 요청해야 하나요?", "계약서사전검토 법무 팀 요청"),
    ("ＶＰＮ 접속 신청은 XX-INF-009 서식으로 제출한다.", "vpn 접속 신청 xx inf 009 서식 제출"),
    ("연차를 반차로 나눠 쓸 수 있나요?", "연차 반차"),
    ("이 규정은 2026-11-20부터 시행하고, 판 2.0.1 로 배포한다.", "규정 2026.11.20 2026 11 20 시행 2.0.1 2 0 1 배포"),
    ("e-러닝 교육을 듣는다", "e러닝 교육"),
    ("문의는 hr-team@example.co.kr 또는 https://intra.example.com 으로 한다.", "문의 으"),
])
def test_contract_examples(text, expected):
    assert " ".join(tokenize_ko(text)) == expected


def test_symbol_variant_matches_either_form():
    assert tokenize_ko("e러닝 교육") == tokenize_ko("e-러닝 교육") == ["e러닝", "교육"]


# 사전 용어 정규화(경계 계약 2.4, 7차 회신 2절 확정)
def test_dictionary_terms_normalized():
    load_dictionary([ConceptTerm("온보딩 체크리스트", ("신입OJT",)), ConceptTerm("방화벽 오픈 신청", ("DB접근권한", "VPN발급")),
                     ConceptTerm("e-러닝 과정")], "norm")
    assert [tokenize_ko(t) for t in ("신입OJT", "DB접근권한", "VPN발급 신청")] == [["신입ojt"], ["db접근권한"], ["vpn발급", "신청"]]
    assert tokenize_ko("e-러닝 과정") == tokenize_ko("e러닝  과정") == ["e러닝과정"]


def test_w_serial_expanded():
    assert tokenize_ko("대표번호 02-123-4567 로 문의") == ["대표", "번호", "02.123.4567", "02", "123", "4567", "문의"]
    assert tokenize_ko("서버 192.168.0.1 접속") == ["서버", "192.168.0.1", "192", "168", "0", "1", "접속"]


def test_spaced_term_joined_any_whitespace():
    for text in ("계약서 사전검토", "계약서   사전검토", "계약서\t사전검토"):
        assert tokenize_ko(text) == ["계약서사전검토"]


def test_spaced_terms_longest_first():
    load_dictionary([ConceptTerm("계약서 사전검토"), ConceptTerm("계약서 사전검토 요청서")], "t")
    assert tokenize_ko("계약서 사전검토 요청서 제출") == ["계약서사전검토요청서", "제출"]


def test_one_char_term_not_added():
    # `연` 을 사전에 넣으면 `연차` 가 `연`·`차` 로 쪼개짐
    load_dictionary([ConceptTerm("연")], "one")
    assert tokenize_ko("연차를 반차로 나눠 쓸 수 있나요?") == ["연차", "반", "차"]


def test_dict_version_and_tsv_source():
    assert current_dict_version() == "t-2.5"
    assert tsv_source("지결 규정", "제1장 > 제2조", "e-러닝 교육") == "지결 규정 1 장 2 조 e러닝 교육"
