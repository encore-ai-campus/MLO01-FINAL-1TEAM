"""한국어 토큰화 `tokenize_ko` 와 사용자 사전 (경계 계약 2절).

적재·재태깅·빌더·질의가 모두 이 모듈 하나를 씁니다.
"""
import re
import unicodedata
from dataclasses import dataclass

from kiwipiepy import Kiwi

KEEP_TAGS = {"NNG", "NNP", "SL", "SN", "SH", "XR"}  # 2.3. W_SERIAL 은 따로 펼침
_FULLWIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}  # U+FF01~FF5E → 반각


@dataclass(frozen=True)
class ConceptTerm:
    canonical_term: str
    synonyms: tuple[str, ...] = ()


# (Kiwi, 사전 버전, 띄어쓰기 든 용어 패턴). 한 참조로 바꿔 요청이 옛·새 사전 중 하나로만 처리되게 함(2.1)
_state: tuple[Kiwi, str, re.Pattern | None] | None = None


def _normalize(text: str) -> str:
    """2.2의 1~3번: 전각 → 반각, NFC, 소문자."""
    return unicodedata.normalize("NFC", text.translate(_FULLWIDTH)).lower()


def _strip_symbols(token: str) -> str:
    """2.2의 8번: 글자·숫자·`.`·`,` 만 남김."""
    return "".join(ch for ch in token if ch.isalnum() or ch in ".,")


def load_dictionary(concepts: list[ConceptTerm], dict_version: str) -> None:
    """사용자 사전(2.4)으로 새 Kiwi 를 다 만든 뒤 프로세스 전역 참조만 바꿉니다."""
    global _state
    words: set[str] = set()
    spaced: set[str] = set()
    for c in concepts:
        for term in (c.canonical_term, *c.synonyms):
            term = _normalize(term)  # 사전 용어도 본문과 같이 정규화(경계 계약 2.4, 7차 회신 2절 확정)
            joined = "".join(term.split())
            if len(joined) < 2:  # 한 글자 용어는 넣지 않음
                continue
            words.add(joined)
            stripped = _strip_symbols(joined)  # 기호 든 용어는 기호 뺀 형태도
            if len(stripped) >= 2:
                words.add(stripped)
            if joined != term:
                spaced.add(" ".join(term.split()))
                bare = " ".join(filter(None, map(_strip_symbols, term.split())))
                if " " in bare:  # 띄어 쓴 기호 용어는 기호 뺀 꼴도 접음(경계 계약 2.4, 7차 회신 2절 확정)
                    spaced.add(bare)
    kiwi = Kiwi()
    for w in sorted(words):
        kiwi.add_user_word(w, "NNP", 0.0)
    kiwi.tokenize("")  # 첫 분석 준비(약 0.6초)를 교체 전에 끝냄
    # 2.2의 4번: 긴 용어부터, 공백 여러 칸도 같은 것으로
    alts = [r"\s+".join(map(re.escape, t.split())) for t in sorted(spaced, key=len, reverse=True)]
    _state = (kiwi, dict_version, re.compile("|".join(alts)) if alts else None)


def current_dict_version() -> str:
    return _loaded()[1]


def _loaded() -> tuple[Kiwi, str, re.Pattern | None]:
    if _state is None:
        raise RuntimeError("load_dictionary 를 먼저 불러야 합니다")
    return _state


def tokenize_ko(text: str) -> list[str]:
    kiwi, _, spaced = _loaded()
    text = _normalize(text)
    if spaced is not None:
        text = spaced.sub(lambda m: "".join(m.group().split()), text)
    out: list[str] = []
    for t in kiwi.tokenize(text):
        tag = t.tag.split("-")[0]
        if tag in KEEP_TAGS:
            out.append(t.form)
        elif tag == "W_SERIAL":  # 2.2의 7번: 2026-11-20 → 2026.11.20 2026 11 20
            parts = re.findall(r"[0-9a-z]+", t.form)
            out += [".".join(parts), *parts]
    return [s for s in map(_strip_symbols, out) if s]


def tsv_source(title: str, section_path: str, content: str) -> str:
    """`chunks.tsv = to_tsvector('simple', 이 글)` 의 글(2.6). tsv 는 이 함수로만 만듭니다."""
    return " ".join(tokenize_ko("\n".join((title, section_path, content))))
