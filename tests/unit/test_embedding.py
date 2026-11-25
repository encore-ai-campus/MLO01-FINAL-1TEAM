import math
from types import SimpleNamespace

import pytest

from rag_api.shared.embedding import EMBED_DIM, OpenAIEmbeddingClient, embed_text


class FakeOpenAI:
    """`client.embeddings.create(...)` 만 흉내 냄. 받은 인자를 남기고 정한 벡터를 돌려줌."""

    def __init__(self, dim=2048):
        self.dim = dim
        self.calls = []
        self.embeddings = self

    def create(self, **kw):
        self.calls.append(kw)
        # i 번째 벡터: 앞 1,024 중 i 자리만 2.0, 뒤는 99. 역순으로 돌려줘 index 로 다시 맞추는지 봄
        data = [SimpleNamespace(index=i, embedding=[2.0 if j == i else 99.0 if j >= EMBED_DIM else 0.0
                                                    for j in range(self.dim)])
                for i in range(len(kw["input"]))]
        return SimpleNamespace(data=data[::-1])


def make(dim=2048):
    fake = FakeOpenAI(dim)
    return OpenAIEmbeddingClient("http://unused", "kure", 0.5, client=fake), fake


def test_query_prefix_once_and_nfc():
    client, fake = make()
    client.embed_query("연차는가?")  # ᄀ+ᅡ 나뉜 자모 → 가
    assert fake.calls == [{"model": "kure", "input": ["query: 연차는가?"], "encoding_format": "float"}]


def test_passages_prefix_once_and_order():
    client, fake = make()
    texts = [embed_text("규정", "ch01", "인사", "제1조", "본문"), embed_text("규정", None, None, "제2조", "둘째")]
    vecs = client.embed_passages(texts)
    assert fake.calls == [{"model": "kure", "input": ["passage: " + t for t in texts], "encoding_format": "float"}]
    assert [v.index(1.0) for v in vecs] == [0, 1]  # 역순 응답을 index 로 맞춤
    assert client.embed_passages([]) == [] and len(fake.calls) == 1


def test_truncate_to_1024_and_unit_length():
    client, _ = make(2048)
    v = client.embed_query("q")
    assert len(v) == EMBED_DIM
    assert math.isclose(math.hypot(*v), 1.0, rel_tol=1e-9)
    assert v[0] == 1.0  # 2.0 → 1.0. 뒤쪽 99 가 섞였으면 작아짐


def test_short_vector_is_error():
    client, _ = make(768)
    with pytest.raises(ValueError):
        client.embed_query("q")


def test_embed_text_format():
    assert embed_text("지출결의 규정", "ch03", "재무·회계", "제2장 정산 > 제6조 (정산 기한)", "본문\n둘째 줄") == \
        "[지출결의 규정 | ch03 재무·회계 | 제2장 정산 > 제6조 (정산 기한)]\n본문\n둘째 줄"
    assert embed_text("t", None, None, "p", "c") == "[t | ch00 공통·미분류 | p]\nc"
    # 빈 문자열은 기본값으로 바꾸지 않음(기준 스크립트는 키가 없을 때만 기본값): 장 ID 와 이름 사이 공백은 그대로
    assert embed_text("t", "", "", "p", "c") == "[t |   | p]\nc" and embed_text("t", "", None, "p", "c") == "[t |  공통·미분류 | p]\nc"


@pytest.mark.parametrize("pos, bad", [(5, float("nan")), (5, float("inf")), (EMBED_DIM + 5, float("-inf"))])
def test_non_finite_is_error(pos, bad):
    client, fake = make()
    create = fake.create

    def poisoned(**kw):
        resp = create(**kw)
        resp.data[0].embedding[pos] = bad
        return resp

    fake.create = poisoned
    with pytest.raises(ValueError):
        client.embed_query("q")


def test_passages_over_the_token_limit_are_logged_not_blocked(kure, caplog):
    fake = FakeOpenAI()
    client = OpenAIEmbeddingClient("http://unused", "kure", 0.5, client=fake, tokenizer=kure, max_tokens=30)
    texts = [embed_text("규정", None, None, "제1조", "짧음"), embed_text("규정", None, None, "제2조", "나" * 50)]
    assert len(client.embed_passages(texts)) == 2  # 넘어도 보냄(서버가 자르지 않게 쪼개기가 막는 것, 3.4)
    assert [r.getMessage() for r in caplog.records] == ["임베딩 입력이 30 토큰을 넘는 조각 1개"]  # 24 토큰짜리는 안 셈


def test_count_embed_tokens_counts_the_prefix_like_the_spec(kure, tmp_path):
    from rag_api.shared.embed_tokens import count_body_tokens, count_embed_tokens, load_tokenizer
    assert count_embed_tokens(kure, "본문\n") == count_embed_tokens(kure, "본문") == len(kure.encode("passage: 본문").ids)
    assert count_body_tokens(kure, "passage: 본문") == 5  # KURE-v3 tokenizer.json 은 특수 토큰을 붙이지 않음(후처리 special_tokens 비어 있음)
    with pytest.raises(FileNotFoundError, match="EMBED_TOKENIZER_PATH"):
        load_tokenizer(tmp_path / "없음.json")
    with pytest.raises(FileNotFoundError):
        load_tokenizer("")
