"""
Шаг 5 агента: самопроверка инсайта вторым вызовом модели (критик) и одна доработка.

Автоматические проверки (ссылки только на переданные источники, опора на признаки компании,
проверки агента через extra_check) работают внутри interpret(). Критик ловит то, что
правилами не поймать: пересказ, общие фразы, вывод модели под видом позиции банка,
незамеченное расхождение новости и аналитики банка.

    from llm.critic import interpret_reviewed
    ins, impact, review = interpret_reviewed(event, news_text, company, bank_chunks=..., role="cfo")
    review["verdict"]      # pass | revised | need_more_data | revise_failed
    review["extra_search"] # шаги поиска для агента, если данных не хватило

Стоимость: +1 вызов GLM на инсайт, +1 при доработке.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from .client import MODELS, CompleteFn, LLMCallError, call_json
from .insight import DEFAULT_ROLE, prepare
from .schemas import CriticVerdict, EventExtraction, Insight

CRITIC_SYSTEM = (Path(__file__).parent / "prompts" / "critic.system.md").read_text(encoding="utf-8")
CRITIC_VERSION = "critic@v2"
# если критик сам поставил pass, но нашёл такое — всё равно дорабатываем (критерий 03 жюри)
BLOCKING = {"wrong_origin", "invented_numbers"}


def critique(ins: Insight, context: str, *, complete: Optional[CompleteFn] = None, model: Optional[str] = None,
             trace: Optional[list] = None) -> CriticVerdict:
    draft = json.dumps(ins.model_dump(mode="json"), ensure_ascii=False)
    msgs = [{"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": f"# ВХОДНЫЕ ДАННЫЕ\n{context}\n\n# ЧЕРНОВИК ИНСАЙТА\n{draft}"}]
    v = call_json(CriticVerdict, msgs, model=model or MODELS["smart"], complete=complete, trace=trace,
                  temperature=0.0, max_tokens=1500)
    # страховка от мягкого критика: блокирующие проблемы всегда ведут к доработке
    if v.verdict == "pass" and any(i.problem in BLOCKING for i in v.issues):
        v.verdict = "revise"
    return v


def interpret_reviewed(event: EventExtraction, news_text: str, company, *, counterparties=(), bank_chunks=(),
                       related_news=(), role: str = DEFAULT_ROLE, complete: Optional[CompleteFn] = None,
                       model: Optional[str] = None, trace: Optional[list] = None,
                       extra_check: Optional[Callable[[Insight], None]] = None) -> tuple[Insight, dict, dict]:
    """interpret → критик → (если revise) одна доработка с замечаниями → итог.

    review: verdict (pass | revised | need_more_data | revise_failed), scores, issues, extra_search.
    need_more_data не переписывает инсайт: агент решает, искать ли ещё и вызвать заново.
    """
    model = model or MODELS["smart"]
    msgs, post_check, impact, context = prepare(event, news_text, company, counterparties=counterparties,
                                                bank_chunks=bank_chunks, related_news=related_news, role=role,
                                                extra_check=extra_check)
    ins = call_json(Insight, msgs, model=model, complete=complete, trace=trace, post_check=post_check,
                    temperature=0.2, max_tokens=4000)

    v = critique(ins, context, complete=complete, model=model, trace=trace)
    review = {"version": CRITIC_VERSION, "verdict": v.verdict, "scores": v.scores,
              "issues": [i.model_dump() for i in v.issues],
              "extra_search": [s.model_dump() for s in v.extra_search]}

    if v.verdict == "revise":
        notes = "\n".join(f"- {i.field}: [{i.problem}] {i.comment}" for i in v.issues)
        fix_msgs = msgs + [
            {"role": "assistant", "content": json.dumps(ins.model_dump(mode="json"), ensure_ascii=False)},
            {"role": "user", "content": "Редактор нашёл проблемы:\n" + notes +
             "\nПерепиши инсайт целиком, исправив их. Остальное сохрани. Верни только JSON."},
        ]
        try:
            ins = call_json(Insight, fix_msgs, model=model, complete=complete, trace=trace, post_check=post_check,
                            temperature=0.2, max_tokens=4000)
            review["verdict"] = "revised"
        except LLMCallError as e:
            review["verdict"] = "revise_failed"   # остаётся первый вариант, он прошёл автопроверки
            review["error"] = str(e)[:300]

    if trace is not None:
        trace.append({"step": "critic", **{k: review[k] for k in ("version", "verdict", "scores")},
                      "issues": [f"{i['field']}: {i['problem']}" for i in review["issues"]]})
    return ins, impact, review
