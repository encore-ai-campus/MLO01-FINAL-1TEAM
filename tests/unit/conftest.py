"""단위 시험 공통 준비."""
import os

import pytest

from rag_api.shared.embed_tokens import load_tokenizer


@pytest.fixture(scope="session")
def kure():
    """KURE-v3 토크나이저(`EMBED_TOKENIZER_PATH`). 없으면 건너뜀. CI 는 고정 판을 받아 넣음(.github/workflows/rag-ci.yml)."""
    path = os.environ.get("EMBED_TOKENIZER_PATH")
    if not path:
        pytest.skip("EMBED_TOKENIZER_PATH 가 없음(토크나이저가 필요한 시험)")
    return load_tokenizer(path)
