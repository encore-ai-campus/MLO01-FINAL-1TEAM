"""메인기획 고정 문구·판별 패턴(rules.md). 값은 `rules.yaml`, 판정은 쓰는 모듈의 코드가 합니다.
숫자 규칙 값(근거 부족 기준값)은 `shared/config.py`."""
import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("rules.yaml")


@dataclass(frozen=True)
class Rules:
    """문구는 str, 패턴은 정규식 목록(하나라도 맞으면 맞음), 안내는 (정규식, 문구) 목록(처음 맞는 것)."""
    cooperation_request: str                          # 협조전 안내. {dept_name} 은 소관 부서 표시 이름
    insufficient_message: str                         # R3 근거 부족 문구
    personal_lookup_patterns: tuple[re.Pattern, ...]  # 개인·실시간 조회(본인 지칭·특정인·실시간 시점)
    case_status_patterns: tuple[re.Pattern, ...]      # 특정 건의 처리 현황 조회 → 근거 부족(LLM 안 부름)
    personal_numeric_patterns: tuple[re.Pattern, ...]  # 개인 수치(5-12)
    procedure_patterns: tuple[re.Pattern, ...]        # 절차 질문 → graph_hybrid
    groupware_guides: tuple[tuple[re.Pattern, str], ...]  # 개인·실시간 조회의 메뉴별 그룹웨어 안내
    groupware_guide_default: str                      # 맞는 메뉴가 없을 때
    evidence_priority_instruction: str                # R1 주·보조 근거 지시(LLM)
    standard_template_header: str                     # R1 인용에 공식 사규가 없고 첫 문서가 표준 양식일 때 머리말. {title}
    benchmark_reference_header: str                   # 같은 때 첫 문서가 외부 참고 사례(benchmark_reference)일 때. {title}
    scheduled_revision_notice: str                    # 5-08 시행 예정 안내. {effective_from}
    legal_disclaimer: str                             # 5-11 면책 문구
    legal_disclaimer_depts: tuple[str, ...]           # 면책 문구를 붙이는 소관 부서(HR·LGL)
    conflict_instruction: str                         # R2 병기 지시(LLM). {a}·{b}·{priority_rule}


def _check_text(where: str, value, placeholders: tuple[str, ...] = ()) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where}: 문자열이 아님")
    if placeholders:  # format 으로 채우는 문구만 시작 때 한 번 채워 봄. 그 밖은 글자 그대로 냄
        try:
            value.format(**dict.fromkeys(placeholders, ""))
        except (KeyError, IndexError, AttributeError, TypeError, ValueError) as e:  # 허용 밖 이름·위치·속성·변환·형식, 깨진 중괄호
            raise ValueError(f"{where}: 문구를 채울 수 없음({type(e).__name__}: {e}). 쓸 수 있는 자리표시자 "
                             f"{sorted(placeholders)}, 글자 그대로의 중괄호는 {{{{ }}}}") from None
    return value


def _compile(where: str, value) -> re.Pattern:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: 비어 있지 않은 문자열이 아님")
    try:
        return re.compile(value)
    except re.error as e:
        raise ValueError(f"{where}: 정규식 오류({e})") from None


def _list(where: str, value) -> list:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: 비어 있지 않은 목록이 아님")
    return value


def load(path: str | Path = "") -> Rules:
    """`path` 가 비면 패키지 안 rules.yaml. 빠진 키·모양이 틀린 값·허용하지 않는 자리표시자·깨진 `{`·`}`·
    정규식 오류는 ValueError(앱이 시작하지 않음). 요청 중에 format·정규식이 실패하지 않게 시작 때 봄."""
    src = f"규칙 파일 {path or DEFAULT_PATH}"
    data = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    data = data if isinstance(data, dict) else {}
    missing = [f.name for f in fields(Rules) if f.name not in data]
    if missing:
        raise ValueError(f"{src}: 없는 키 {missing}")
    text = lambda k, placeholders=(): _check_text(f"{src}: {k}", data[k], placeholders)
    pats = lambda k: tuple(_compile(f"{src}: {k}[{i}]", v) for i, v in enumerate(_list(f"{src}: {k}", data[k])))
    texts = lambda k: tuple(_check_text(f"{src}: {k}[{i}]", v) for i, v in enumerate(_list(f"{src}: {k}", data[k])))
    guides = []
    for i, g in enumerate(_list(f"{src}: groupware_guides", data["groupware_guides"])):
        where = f"{src}: groupware_guides[{i}]"
        if not isinstance(g, dict) or set(g) != {"pattern", "text"}:
            raise ValueError(f"{where}: pattern·text 두 키가 아님")
        guides.append((_compile(f"{where}.pattern", g["pattern"]), _check_text(f"{where}.text", g["text"])))
    return Rules(cooperation_request=text("cooperation_request", ("dept_name",)),
                 insufficient_message=text("insufficient_message"),
                 personal_lookup_patterns=pats("personal_lookup_patterns"),
                 case_status_patterns=pats("case_status_patterns"),
                 personal_numeric_patterns=pats("personal_numeric_patterns"),
                 procedure_patterns=pats("procedure_patterns"),
                 groupware_guides=tuple(guides), groupware_guide_default=text("groupware_guide_default"),
                 evidence_priority_instruction=text("evidence_priority_instruction"),
                 standard_template_header=text("standard_template_header", ("title",)),
                 benchmark_reference_header=text("benchmark_reference_header", ("title",)),
                 scheduled_revision_notice=text("scheduled_revision_notice", ("effective_from",)),
                 legal_disclaimer=text("legal_disclaimer"), legal_disclaimer_depts=texts("legal_disclaimer_depts"),
                 conflict_instruction=text("conflict_instruction", ("a", "b", "priority_rule")))
