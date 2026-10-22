"""Локальный BM25 без зависимостей.

Нужен как запасной поиск по 9 тезисам банка, пока индекс бэкенда не подключён,
и как план Б, если модель эмбеддингов окажется под запретом (см. риски в плане).
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP = set("и в во на по с со к ко о об от до из за для не что как а но или же ли бы это его ее их при под над без".split())


def tokenize(text: str) -> list[str]:
    toks = [t.lower().replace("ё", "е") for t in _TOKEN.findall(text)]
    # грубый стемминг: обрезаем до 6 символов, этого хватает для русских окончаний
    return [t[:6] for t in toks if t not in _STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokenize(d) for d in docs]
        self.avgdl = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out
