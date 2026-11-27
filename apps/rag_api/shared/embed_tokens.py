"""임베딩 입력 토큰 수 세기 (경계 계약 3.4).

임베딩 모델과 같은 토크나이저(`tokenizer.json`)로 셉니다. 추정값으로 대신하지 않습니다(환경마다 조각이 달라짐).
파일은 모델 파일이라 저장소에 두지 않고 `EMBED_TOKENIZER_PATH` 에서 읽습니다. 문서 조각을 임베딩하는 쪽(파이프라인·시드)만 씁니다.
"""
from pathlib import Path

from tokenizers import Tokenizer

PASSAGE_PREFIX = "passage: "  # 임베딩 함수가 문서 조각에 붙이는 접두어(3.2). 셀 때도 같이 셈


def load_tokenizer(path: str | Path) -> Tokenizer:
    """경로가 비었거나 파일이 없으면 FileNotFoundError. 자르기·채우기는 끔(잘라서 세면 넘는 조각을 못 찾음)."""
    if not path or not Path(path).is_file():
        raise FileNotFoundError(f"EMBED_TOKENIZER_PATH: 토크나이저 파일이 없습니다 ({path!r})")
    tok = Tokenizer.from_file(str(path))
    tok.no_truncation()
    tok.no_padding()
    return tok


def count_embed_tokens(tok: Tokenizer, embed_text: str) -> int:
    """`passage: ` + 글을 토크나이저 기본값으로 센 수(규격과 같은 셈). 특수 토큰은 토크나이저 후처리가 붙이는 만큼만 들어가는데
    KURE-v3 tokenizer.json 은 붙이지 않음. 실행기가 붙일 수 있는 시작·끝 토큰은 쪼개기 예산의 여유 2 토큰이 덮음(5.3).
    끝 줄바꿈은 서버 입력에 없으므로 뗌."""
    return len(tok.encode(PASSAGE_PREFIX + embed_text.rstrip("\n")).ids)


def count_body_tokens(tok: Tokenizer, text: str) -> int:
    """글만의 토큰 수(특수 토큰 없음). 쪼개기의 본문 예산 판정(5.3)."""
    return len(tok.encode(text, add_special_tokens=False).ids)
