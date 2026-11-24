"""메인기획 문구·패턴 파일(rules.yaml) 읽기(rules.md)."""
import pytest
import yaml

from rag_api.rules import values


def rules_file(tmp_path, drop=(), **override):
    """패키지 rules.yaml 을 복사해 `override` 키만 바꾸고 `drop` 키는 뺀 파일."""
    data = yaml.safe_load(values.DEFAULT_PATH.read_text(encoding="utf-8"))
    data = {k: v for k, v in {**data, **override}.items() if k not in drop}
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_packaged_rules_load():
    r = values.load()  # 빈 경로 = 패키지 안 rules.yaml
    assert r == values.load(values.DEFAULT_PATH)
    assert r.cooperation_request.format(dept_name="인사·노무") == (
        "본 문서는 인사·노무 열람 제한 규정입니다. 열람이 필요한 경우 사내 그룹웨어에서 인사·노무 앞으로 "
        "'문서 열람 협조전'을 신청해 주시기 바랍니다.")  # 약속서 00 4.2·03A 4절 예시
    assert r.insufficient_message == "질문하신 내용과 일치하는 사내 규정 근거를 찾지 못했습니다."  # 03B 3.4
    assert r.groupware_guides[0][1] == "개인별 잔여 연차 일수는 사내 그룹웨어 근태/휴가 메뉴에서 확인해 주세요."  # 5-12
    assert all(len(r.__dict__[k]) for k in ("personal_lookup_patterns", "procedure_patterns", "personal_numeric_patterns"))
    # qa 2단계 문구(03B 3.1·3.3 예시와 같은 문장)
    assert r.standard_template_header.format(title="양식") == "현재 자사 확정 사규가 없어 공공 표준 양식([양식])을 기준으로 안내합니다."
    assert r.scheduled_revision_notice.format(effective_from="2027-01-01") == "[안내] 본 규정은 2027-01-01 시행 예정인 개정판 기준입니다."
    assert r.legal_disclaimer == ("※ 본 답변은 사내 규정 원문에 기반한 안내이며 최종 법적·노무적 유권해석은 인사/법무 담당 부서의 "
                                  "확인이 필요합니다.") and r.legal_disclaimer_depts == ("HR", "LGL")
    assert "{priority_rule}" in r.conflict_instruction


@pytest.mark.parametrize("text", ["", "- a\n", "other: 1\n"])
def test_not_a_mapping_or_missing_keys_fails(tmp_path, text):
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="없는 키"):
        values.load(path)


@pytest.mark.parametrize("key", ["cooperation_request", "procedure_patterns", "groupware_guides", "legal_disclaimer_depts",
                                 "conflict_instruction"])
def test_each_key_required(tmp_path, key):
    with pytest.raises(ValueError, match=f"없는 키 \\['{key}'\\]"):
        values.load(rules_file(tmp_path, drop=(key,)))


@pytest.mark.parametrize("override, error", [
    ({"cooperation_request": 3}, "cooperation_request: 문자열이 아님"),
    ({"insufficient_message": None}, "insufficient_message: 문자열이 아님"),
    ({"procedure_patterns": []}, "procedure_patterns: 비어 있지 않은 목록"),
    ({"procedure_patterns": "순서"}, "procedure_patterns: 비어 있지 않은 목록"),
    ({"personal_lookup_patterns": ["("]}, r"personal_lookup_patterns\[0\]: 정규식 오류"),
    ({"personal_numeric_patterns": [""]}, r"personal_numeric_patterns\[0\]: 비어 있지 않은 문자열"),
    ({"groupware_guides": [{"pattern": "연차"}]}, r"groupware_guides\[0\]: pattern·text"),
    ({"groupware_guides": [{"pattern": "[", "text": "x"}]}, r"groupware_guides\[0\].pattern: 정규식 오류"),
    ({"groupware_guides": [{"pattern": "연차", "text": 1}]}, r"groupware_guides\[0\].text: 문자열이 아님"),
    ({"legal_disclaimer_depts": []}, "legal_disclaimer_depts: 비어 있지 않은 목록"),
    ({"legal_disclaimer_depts": ["HR", 3]}, r"legal_disclaimer_depts\[1\]: 문자열이 아님"),
    ({"standard_template_header": "양식({name})"}, "standard_template_header: 문구를 채울 수 없음"),
    ({"scheduled_revision_notice": "{effective_from:%Y}"}, "scheduled_revision_notice: 문구를 채울 수 없음"),
    ({"conflict_instruction": "{a} {b} {rule}"}, "conflict_instruction: 문구를 채울 수 없음")])
def test_bad_values_fail_at_load(tmp_path, override, error):
    with pytest.raises(ValueError, match=error):
        values.load(rules_file(tmp_path, **override))


@pytest.mark.parametrize("text", [
    "{dept} 에 문의", "협조전 {", "협조전 }", "{} 에 문의", "{0} 에 문의", "{dept_name.x} 에 문의", "{dept_name[0]} 에 문의",
    "{dept_name:{x}} 에 문의", "{dept_name!z} 에 문의", "{dept_name:d} 에 문의"])  # 이름·위치·속성·인덱스·중첩·변환·형식
def test_bad_placeholder_fails_at_load(tmp_path, text):
    with pytest.raises(ValueError, match="cooperation_request: 문구를 채울 수 없음"):
        values.load(rules_file(tmp_path, cooperation_request=text))


@pytest.mark.parametrize("text", ["{dept_name} 에 문의", "문구만", "{{글자}} {dept_name}", "{dept_name:>4}·{dept_name!r}"])
def test_allowed_placeholders_load(tmp_path, text):
    r = values.load(str(rules_file(tmp_path, cooperation_request=text)))  # 문자열 경로도 됨
    assert r.cooperation_request.format(dept_name="재무") == text.format(dept_name="재무")


def test_unformatted_text_keeps_braces(tmp_path):
    r = values.load(rules_file(tmp_path, insufficient_message="근거 {없음}"))  # format 하지 않는 문구는 글자 그대로
    assert r.insufficient_message == "근거 {없음}"
