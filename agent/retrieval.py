"""Лексический поиск без зависимостей: грубый русский стеммер + BM25.

Где используется:
  • поиск по тезисам банка, пока у бэкенда нет индекса;
  • близость «новость ↔ профиль компании» в скоринге;
  • сопоставление сущностей компании (страны ВЭД, сырьё, контрагенты) с текстом новости.
Это и есть план Б из рисков: если модель эмбеддингов окажется под запретом, поиск остаётся на BM25.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_STOP = set(
    "и в во на по с со к ко о об от до из за для не что как а но или же ли бы это его ее их при под над без "
    "также уже ещё еще было были будет может могут году года годом более менее около после "
    "the of and to in".split())
# окончания по убыванию длины: отрезаем одно самое длинное; основа — не короче 4 букв
# (для однобуквенного окончания — 3), иначе «Китая» и «стали» теряют корень
_ENDINGS = sorted(
    "иями ями ами ией ого его ому ему ыми ими иях ях ах ых их ым им ия ие ии ию ая яя ое ее ые ой ей ий ый ую юю "
    "ом ем ам ям ов ев ть ла ло ли ет ют ат ят а я о е ы и у ю ь й".split(), key=len, reverse=True)


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if w.isdigit():
        return w
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= (4 if len(e) > 1 else 3):
            w = w[: -len(e)]
            break
    return w[:8]


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text) if t.lower() not in _STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokenize(d) for d in docs]
        self.avgdl = sum(map(len, self.docs)) / max(len(self.docs), 1) or 1.0
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = set(tokenize(query))
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


def jaccard(a: str, b: str) -> float:
    """Похожесть двух текстов по множествам основ — для подавления дублей в ленте."""
    x, y = set(tokenize(a)), set(tokenize(b))
    return len(x & y) / len(x | y) if x and y else 0.0
