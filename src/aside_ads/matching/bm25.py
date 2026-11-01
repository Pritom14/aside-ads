"""Pure-Python BM25 relevance, used when no embedding model is available."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache

from aside_ads.matching import AdDoc, ContextAnalysis
from aside_ads.text import tokenize


@lru_cache(maxsize=4096)
def _doc_terms(text: str) -> tuple[tuple[str, int], ...]:
    return tuple(Counter(tokenize(text, drop_stopwords=True)).items())


class BM25Matcher:
    """BM25 over the candidate ads, squashed to [0, 1) with ``s / (s + saturation)``.

    IDF is computed as if the corpus also held ``background_docs`` unrelated
    documents, so scores stay comparable whether you run 3 campaigns or 300.
    With the defaults one shared distinctive term scores about 0.4, two score
    about 0.57 and three about 0.67, so the default threshold (0.5) asks for at
    least two meaningful terms in common.
    """

    name = "bm25"
    default_threshold = 0.5

    def __init__(
        self, k1: float = 1.2, b: float = 0.75, saturation: float = 4.0, background_docs: int = 20
    ) -> None:
        self.k1 = k1
        self.b = b
        self.saturation = saturation
        self.background_docs = background_docs

    def relevance(self, ctx: ContextAnalysis, docs: Sequence[AdDoc]) -> list[float]:
        if not docs:
            return []
        query = set(ctx.tokens)
        if not query:
            return [0.0] * len(docs)
        tfs = [dict(_doc_terms("\n".join(d.lines))) for d in docs]
        lengths = [sum(tf.values()) or 1 for tf in tfs]
        avgdl = sum(lengths) / len(lengths)
        n_docs = len(docs) + self.background_docs
        df: Counter[str] = Counter()
        for tf in tfs:
            df.update(t for t in tf if t in query)
        scores: list[float] = []
        for tf, dl in zip(tfs, lengths, strict=True):
            s = 0.0
            for term in query:
                f = tf.get(term)
                if not f:
                    continue
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / avgdl))
            scores.append(s / (s + self.saturation) if s > 0 else 0.0)
        return scores

    def exemplar_similarity(self, ctx: ContextAnalysis, exemplars: Sequence[str]) -> float | None:
        return None
