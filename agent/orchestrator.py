"""Агентный оркестратор:
профиль → план → поиск новостей → разметка (если источник не разметил) → скоринг с учётом роли →
аналитика банка → интерпретация (промпт промпт-инженера) → самопроверка → карточки в формате бэкенда.

Каждый шаг пишется в trace: что вызвали, с чем, что получили, сколько заняло, был ли откат.
Бэкенд вызывает run() из фоновой задачи refresh (backend/app/ml_ports/agent_port.py).
Критик (llm.critic) проверяет каждую карточку; если он отвечает «данных не хватает» и предлагает,
что поискать (extra_search), агент выполняет эти шаги один раз и пишет карточку заново (_repeat_search).
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import llm.client as llm_client
from llm.client import LLMCallError
from llm.extract import extract_event
from llm.okved.sensitivity import RU as FACTOR_RU

from .config import Settings
from .gateway import Gateway, LLMUnavailable
from .generator import InsightContext, generate, mark_insufficient, to_backend
from .profiles import ProfileStore
from .roles import Role, get_role
from .schemas import AgentResult, CompanyProfile, NewsItem, TraceStep
from .entities import company_terms
from .retrieval import BM25, jaccard
from .scoring import ScoreBreakdown, company_query, explain, score_news
from .tools import NewsProvider, Tools

MAX_PERIOD_DAYS = 90
MACRO_FALLBACK_N = 2
RELATED_NEWS_N = 2      # сколько связанных новостей добавляет повторный поиск
DUP_JACCARD = 0.35      # общая доля основ заголовка+текста, выше — считаем перепечаткой одного события


class CompanyNotFound(LookupError):
    pass


class Orchestrator:
    def __init__(self, settings: Settings | None = None, news_provider: Optional[NewsProvider] = None):
        self.s = settings or Settings.from_env()
        llm_client.CACHE_DIR = Path(self.s.llm_cache_dir)   # общий кэш call_json — его же читает replay
        self.today = date.fromisoformat(self.s.today) if self.s.today else date.today()
        self.tools = Tools(self.today, news_provider)
        self._profiles: ProfileStore | None = None

    def profile(self, company_id: str) -> CompanyProfile:
        """Профиль из CSV — для запуска без бэкенда. Бэкенд передаёт профиль сам."""
        if self._profiles is None:
            self._profiles = ProfileStore(self.s.companies_csv)
        p = self._profiles.get(company_id)
        if p is None:
            raise CompanyNotFound(f"компания {company_id!r} не найдена в {self.s.companies_csv}")
        return p

    @contextmanager
    def _step(self, trace: list[TraceStep], name: str, tool: str | None = None, **inp: Any) -> Iterator[TraceStep]:
        st = TraceStep(step=len(trace) + 1, name=name, tool=tool, input=inp)
        t0 = time.perf_counter()
        try:
            yield st
        except Exception as e:
            st.status, st.note = "error", f"{type(e).__name__}: {e}"
            raise
        finally:
            st.duration_ms = int((time.perf_counter() - t0) * 1000)
            trace.append(st)

    # ---------- главный цикл ----------
    def run(self, company: CompanyProfile | str, period_days: int = 30, top_k: int | None = None,
            role: str | None = None) -> AgentResult:
        r = get_role(role)
        top_k = top_k or self.s.top_k_news
        gw = Gateway(self.s)
        llm_trace: list[dict] = []
        trace: list[TraceStep] = []
        cid = company if isinstance(company, str) else company.company_id
        run_id = f"{cid}-{r.key}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"

        with self._step(trace, "Профиль компании", "get_company_profile", company_id=cid) as st:
            profile = self.profile(company) if isinstance(company, str) else company
            st.output = {"profile": profile.short(), "facts": profile.facts()}
            if not profile.okved2:
                st.status, st.note = "fallback", "ОКВЭД не найден в карте отраслей: влияние через макрофакторы не посчитать"

        with self._step(trace, "План анализа", role=r.key) as st:
            plan = self._plan(profile, period_days, r)
            st.output = {"plan": plan}

        news, period_used = self._retrieve(trace, cid, period_days)
        terms = company_terms(profile.row)
        sims = self._prefilter(trace, profile, news, terms)
        self._extract_missing(trace, news, sims, gw, llm_trace)

        with self._step(trace, "Скоринг и отбор", "score_news", n_candidates=len(news), top_k=top_k,
                        min_relevance=self.s.min_relevance, role=r.key) as st:
            ranked = sorted(((n, score_news(profile, n, period_used, self.today, r, terms, sims.get(n.news_id, 0.0)))
                             for n in news), key=lambda x: -x[1].score)
            selected, dups = self._select(ranked, top_k)
            if not selected:
                # без ОКВЭД или в тихий период: общие макрособытия (ставка ЦБ первой), с пометкой
                macro = [(n, b) for n, b in ranked if n.event and not n.event.is_noise
                         and not n.event.okved_hints and n.event.factor_moves]
                macro.sort(key=lambda x: (x[0].event.event_type.value == "monetary_policy", x[0].published_at),
                           reverse=True)
                selected, more = self._select(macro, MACRO_FALLBACK_N, min_score=0.0)
                dups.update(more)
                st.status = "fallback" if selected else "skipped"
                st.note = ("ни одна новость не прошла порог; показаны общие макрособытия" if selected
                           else "ни одна новость не прошла порог релевантности")
            chosen = {n.news_id for n, _ in selected}
            st.output = [{"news_id": n.news_id, "title": n.title, "score": b.score, "parts": b.parts,
                          "macro_sign": b.macro.get("sign"), "entities": [t.text for t in b.entities],
                          "selected": n.news_id in chosen, "duplicate_of": dups.get(n.news_id)}
                         for n, b in ranked]

        insights = [self._analyze(trace, gw, llm_trace, profile, n, b, r, run_id, news) for n, b in selected]

        usage = {**gw.stats, "cache_hits": sum(1 for x in llm_trace if x.get("cached")),
                 "schema_retries": sum(1 for x in llm_trace if x.get("ok") is False), "calls_log": llm_trace}
        result = AgentResult(run_id=run_id, company_id=cid, role=r.key, period_days=period_used,
                             generated_at=datetime.now(), llm_mode=self.s.llm_mode, profile=profile, plan=plan,
                             insights=insights, trace=trace, llm_usage=usage)
        self._save(result)
        return result

    # ---------- шаги ----------
    def _plan(self, p: CompanyProfile, period_days: int, role: Role) -> list[str]:
        products = [t for i, t in p.facts().items() if i.startswith("F:CL_PRODUCT") or i in ("F:FTS_FLAG", "F:FACTORING_MARK")]
        return [
            f"Читатель: {role.title.lower()} — {role.focus.rstrip('.').lower()}",
            f"Найти новости за {period_days} дн.; отрасль «{p.industry or 'н/д'}» (ОКВЭД {p.okved2 or 'н/д'}), "
            f"регион «{p.region or 'н/д'}»",
            "Оценить влияние через макрофакторы с учётом продуктов банка: " + (", ".join(products).lower() or "нет данных"),
            f"Отобрать до {self.s.top_k_news} событий с релевантностью ≥ {self.s.min_relevance}",
            "Подобрать тезисы аналитики Альфа-Банка к каждому событию",
            "Собрать карточку из 6 блоков с разметкой факт / позиция банка / вывод ИИ",
            "Проверить числа, источники и цитаты банка; при ошибке — исправление моделью или откат на правила",
        ]

    def _retrieve(self, trace: list[TraceStep], company_id: str, period: int) -> tuple[list[NewsItem], int]:
        while True:
            with self._step(trace, "Поиск новостей", "get_relevant_news", company_id=company_id, period=period) as st:
                news, src = self.tools.get_relevant_news(company_id, period)
                st.output = {"n": len(news), "source": src, "ids": [n.news_id for n in news]}
                if src.startswith("mock"):
                    st.status, st.note = "fallback", src
            if news or period >= MAX_PERIOD_DAYS:
                return news, period
            period = min(period * 2, MAX_PERIOD_DAYS)   # повторный поиск с расширенным окном

    def _prefilter(self, trace: list[TraceStep], profile: CompanyProfile, news: list[NewsItem],
                   terms: list) -> dict[str, float]:
        """Дешёвый первый этап до вызова модели: близость к профилю (BM25) и упоминание сущностей.
        По нему выбираются неразмеченные новости, которые стоит отдать на разметку (она платная)."""
        with self._step(trace, "Отбор кандидатов", "prefilter", n=len(news), n_terms=len(terms)) as st:
            query = company_query(profile)
            raw = BM25([f"{n.title}. {n.text}" for n in news]).scores(query) if news and query else []
            top = max(raw, default=0.0) or 1.0
            sims = {n.news_id: round(x / top, 3) for n, x in zip(news, raw)}
            st.output = {"query": query, "terms": [f"{t.text} ({t.source_id})" for t in terms],
                         "top_similar": sorted(sims.items(), key=lambda x: -x[1])[:5]}
        return sims

    def _select(self, ranked: list, k: int, min_score: float | None = None) -> tuple[list, dict[str, str]]:
        """Top-k с порогом и подавлением дублей: одно событие от двух изданий — одна карточка."""
        thr = self.s.min_relevance if min_score is None else min_score
        chosen: list = []
        dups: dict[str, str] = {}
        for n, b in ranked:
            if b.score < thr or len(chosen) >= k:
                continue
            twin = next((c for c, _ in chosen if jaccard(f"{n.title} {n.text}", f"{c.title} {c.text}") >= DUP_JACCARD), None)
            if twin is not None:
                dups[n.news_id] = twin.news_id
                continue
            chosen.append((n, b))
        return chosen, dups

    def _extract_missing(self, trace: list[TraceStep], news: list[NewsItem], sims: dict[str, float],
                         gw: Gateway, llm_trace: list) -> None:
        todo = sorted((n for n in news if n.event is None), key=lambda n: -sims.get(n.news_id, 0.0))
        if not todo:
            return
        with self._step(trace, "Разметка новостей", "extract_event", n=len(todo)) as st:
            if self.s.llm_mode == "stub":
                st.status, st.note = "skipped", "LLM_MODE=stub: неразмеченные новости не ранжируются"
                return
            done, failed = [], []
            for n in todo[: self.s.max_extract_per_run]:
                try:
                    n.event = extract_event({"id": n.news_id, "title": n.title, "text": n.text, "source": n.source,
                                             "url": n.url, "published_at": n.published_at.isoformat()},
                                            complete=gw.complete, trace=llm_trace)
                    done.append(n.news_id)
                except (LLMUnavailable, LLMCallError) as e:
                    failed.append({"news_id": n.news_id, "error": str(e)[:200]})
            st.output = {"extracted": done, "failed": failed,
                         "skipped_over_limit": len(todo) - len(done) - len(failed)}
            if failed:
                st.status = "fallback"

    def _analyze(self, trace: list[TraceStep], gw: Gateway, llm_trace: list, profile: CompanyProfile,
                 news: NewsItem, b: ScoreBreakdown, role: Role, run_id: str, pool: list[NewsItem] = ()) -> dict:
        why = explain(profile, b)
        factors = [w["factor"] for w in why if w.get("factor")] or \
            [m.factor.value for m in (news.event.factor_moves if news.event else [])]
        query = f"{news.title}. " + " ".join(FACTOR_RU.get(f, f) for f in factors)
        with self._step(trace, f"Аналитика банка: {news.news_id}", "search_bank_analytics",
                        query=query, factors=factors, okved2=profile.okved2) as st:
            hits, src = self.tools.search_bank_analytics(query, factors, k=self.s.theses_per_news,
                                                         okved2=profile.okved2)
            st.output = {"source": src, "hits": [{"id": t.sid, "score": s} for t, s in hits]}
            if not hits:
                st.note = "в аналитике банка нет тезиса по теме события и отрасли компании"
        use_critic = self.s.llm_use_critic and self.s.llm_mode != "stub"
        ctx = InsightContext(profile=profile, news=news, breakdown=b, why=why, theses=[t for t, _ in hits], role=role)
        with self._step(trace, f"Интерпретация и самопроверка: {news.news_id}", "interpret",
                        news_id=news.news_id, role=role.key, mode=self.s.llm_mode, critic=use_critic) as st:
            n0 = len(llm_trace)
            ins, meta = generate(ctx, gw, self.s.llm_use_smart, llm_trace, use_critic)
            st.output = self._gen_output(meta, ins, llm_trace[n0:])
            if meta["path"] == "rules" and self.s.llm_mode != "stub":
                st.status, st.note = "fallback", meta.get("fallback_reason")

        reviews = [meta["review"]] if meta.get("review") else []
        if reviews and reviews[0]["verdict"] == "need_more_data":
            ctx2 = self._repeat_search(trace, ctx, reviews[0], factors, pool)
            if ctx2 is not None:
                with self._step(trace, f"Карточка заново с новыми данными: {news.news_id}", "interpret",
                                news_id=news.news_id, theses=[t.sid for t in ctx2.theses],
                                related=[n.sid for n in ctx2.related]) as st:
                    n0 = len(llm_trace)
                    ins2, meta2 = generate(ctx2, gw, self.s.llm_use_smart, llm_trace, use_critic)
                    st.output = self._gen_output(meta2, ins2, llm_trace[n0:])
                    if meta2["path"] == "llm":
                        ins, ctx = ins2, ctx2
                        meta = {**meta2, "notes": meta["notes"] + ["повторный поиск: карточка переписана"]
                                + meta2["notes"]}
                        if meta2.get("review"):
                            reviews.append(meta2["review"])
                    else:   # второй проход не удался — оставляем первую карточку, она прошла автопроверки
                        st.status, st.note = "fallback", meta2.get("fallback_reason")
            if reviews[-1]["verdict"] == "need_more_data":
                mark_insufficient(ins, reviews[-1])

        extra = {"run_id": run_id, "relevance": b.score, "score_parts": b.parts,
                 "macro_sign": b.macro.get("sign", "neutral"), "validation_notes": meta.get("notes", [])}
        if reviews:
            extra["review"] = {**{k: reviews[-1].get(k) for k in ("version", "verdict", "scores", "issues")},
                               "rounds": [r.get("verdict") for r in reviews],
                               "related_news": [n.sid for n in ctx.related]}
        return to_backend(ins, ctx, self.tools.resolve_source(news, ctx.related), meta["path"], extra)

    @staticmethod
    def _gen_output(meta: dict, ins, calls: list) -> dict:
        out = {k: v for k, v in meta.items() if k != "review"}
        if meta.get("review"):
            out["critic"] = {k: meta["review"].get(k) for k in ("verdict", "scores", "issues", "extra_search", "error")
                             if meta["review"].get(k) is not None}
        out["llm_calls"] = calls
        out["blocks"] = {k: len(getattr(ins, k)) for k in
                         ("what_happened", "why_it_matters", "consequences", "actions", "bank_view")}
        return out

    def _repeat_search(self, trace: list[TraceStep], ctx: InsightContext, review: dict, factors: list[str],
                       pool: list[NewsItem]) -> InsightContext | None:
        """Шаги, которые предложил критик (до двух), выполняем один раз. Нашлось новое — контекст для
        второго прохода; не нашлось — None, карточка остаётся с пометкой «данных мало»."""
        steps = (review.get("extra_search") or [])[:2]
        news, profile = ctx.news, ctx.profile
        with self._step(trace, f"Повторный поиск: {news.news_id}", "extra_search", steps=steps) as st:
            have = {t.sid for t in ctx.theses}
            theses, related, done = list(ctx.theses), list(ctx.related), []
            for s in steps:
                tool, q = s.get("tool"), _query_of(s)
                if tool == "search_bank_analytics" and q:
                    hits, _ = self.tools.search_bank_analytics(q, factors, k=self.s.theses_per_news,
                                                               okved2=profile.okved2)
                    new = [t for t, _ in hits if t.sid not in have]
                    have |= {t.sid for t in new}
                    theses += new
                    done.append({"tool": tool, "query": q, "found": [t.sid for t in new]})
                elif tool == "search_news" and q:
                    new = self._related_news(news, q, pool, {n.news_id for n in related})
                    related += new
                    done.append({"tool": tool, "query": q, "found": [n.sid for n in new]})
                elif tool == "get_company_profile":
                    done.append({"tool": tool, "found": [], "note": "все признаки компании уже переданы модели"})
                else:
                    done.append({"tool": tool, "found": [], "note": "источник не подключён"})
            st.output = {"steps": done}
            if len(theses) == len(ctx.theses) and len(related) == len(ctx.related):
                st.status, st.note = "skipped", "новых данных не нашлось — карточка остаётся с пометкой «данных мало»"
                return None
        return InsightContext(profile=profile, news=news, breakdown=ctx.breakdown, why=ctx.why, theses=theses,
                              role=ctx.role, related=related)

    @staticmethod
    def _related_news(news: NewsItem, query: str, pool: list[NewsItem], skip: set[str]) -> list[NewsItem]:
        """Новости того же периода по запросу критика (BM25); шум и сама новость не берутся."""
        others = [n for n in pool if n.news_id != news.news_id and n.news_id not in skip
                  and not (n.event and n.event.is_noise)]
        if not others:
            return []
        raw = BM25([f"{n.title}. {n.text}" for n in others]).scores(query)
        top = max(raw, default=0.0)
        if top <= 0:
            return []
        ranked = sorted(zip(others, raw), key=lambda x: -x[1])
        return [n for n, x in ranked if x >= 0.5 * top][:RELATED_NEWS_N]

    def _save(self, result: AgentResult) -> None:
        try:
            d = Path(self.s.runs_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{result.run_id}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            pass   # в контейнере с read-only файловой системой прогон не сохраняем — это не ошибка агента


def _query_of(step: dict) -> str:
    """Текст запроса из шага критика: args.query / q / topic, иначе все значения args, иначе goal."""
    args = step.get("args") or {}
    for k in ("query", "q", "topic", "text"):
        if isinstance(args.get(k), str) and args[k].strip():
            return args[k].strip()
    vals = " ".join(str(v) for v in args.values() if isinstance(v, (str, int, float)))
    return (vals or step.get("goal") or "").strip()
