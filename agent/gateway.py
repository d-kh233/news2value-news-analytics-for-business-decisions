"""LLM-шлюз команды: функция `complete` для llm.client.call_json — режимы, запись ответов,
дневной лимит и лог расхода.

  stub   — вызовы запрещены (оркестратор даже не зовёт модель);
  live   — проверка лимита 500 ₽/день, вызов OpenAI-совместимого API, запись ответа и расхода;
  replay — ответ только из записи; нет записи → LLMUnavailable, агент откатывается на правила.

Зачем своя запись, если у call_json есть кэш: тот хранит только ответы, прошедшие проверку.
Если модель исправлялась после самопроверки, первый (неудачный) ответ там не сохраняется,
и в replay до исправления не дойти. Здесь пишется каждый ответ — replay повторяет весь диалог.

Если бэкенд выгрузит свой LLM-шлюз с той же сигнатурой complete(model, messages, **params) -> str,
его можно передать в оркестратор вместо этой функции.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import date
from pathlib import Path

from .config import Settings


class LLMUnavailable(RuntimeError):
    """Модель не вызывается: режим stub, промах кэша в replay, нет ключа, лимит, сбой сети."""


class BudgetExceeded(LLMUnavailable):
    pass


class Gateway:
    def __init__(self, s: Settings):
        self.s = s
        self.dir = Path(s.llm_cache_dir)
        self.stats = {"calls": 0, "replayed": 0, "tokens": 0, "cost_rub_est": 0.0, "errors": 0, "missed": 0}

    # ---------- запись ответов ----------
    def _record_path(self, model: str, messages: list[dict], params: dict) -> Path:
        raw = json.dumps({"m": model, "msg": messages, "p": params}, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return self.dir / "calls" / key[:2] / f"{key}.json"

    # ---------- учёт расхода ----------
    def _usage_path(self) -> Path:
        return self.dir / f"usage-{date.today().isoformat()}.json"

    def spent_today(self) -> float:
        p = self._usage_path()
        return json.loads(p.read_text())["cost_rub_est"] if p.exists() else 0.0

    def _record(self, model: str, tokens: int) -> None:
        cost = tokens / 1000 * self.s.llm_price_rub_per_1k_tokens
        p = self._usage_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        u = json.loads(p.read_text()) if p.exists() else {"cost_rub_est": 0.0, "tokens": 0, "log": []}
        u["cost_rub_est"] = round(u["cost_rub_est"] + cost, 4)
        u["tokens"] += tokens
        u["log"].append({"ts": time.strftime("%H:%M:%S"), "model": model, "tokens": tokens,
                         "who": os.environ.get("TEAM_MEMBER", os.environ.get("USER", "?"))})
        p.write_text(json.dumps(u, ensure_ascii=False, indent=1))
        self.stats["calls"] += 1
        self.stats["tokens"] += tokens
        self.stats["cost_rub_est"] = round(self.stats["cost_rub_est"] + cost, 4)

    # ---------- вызов ----------
    def complete(self, model: str, messages: list[dict], temperature: float = 0.1,
                 max_tokens: int = 1500, **_: object) -> str:
        if self.s.llm_mode == "stub":
            raise LLMUnavailable("LLM_MODE=stub")
        rec = self._record_path(model, messages, {"t": temperature, "max": max_tokens})
        if rec.exists():
            self.stats["replayed"] += 1
            return json.loads(rec.read_text(encoding="utf-8"))["reply"]
        if self.s.llm_mode == "replay":
            self.stats["missed"] += 1
            raise LLMUnavailable("нет записанного ответа, а в replay новые вызовы запрещены")
        if not (self.s.llm_base_url and self.s.llm_api_key):
            raise LLMUnavailable("LLM_BASE_URL / LLM_API_KEY не заданы")
        if self.spent_today() >= self.s.llm_daily_budget_rub:
            raise BudgetExceeded(f"дневной лимит {self.s.llm_daily_budget_rub} ₽ исчерпан")

        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if os.environ.get("LLM_JSON_MODE", "1") == "1":
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            f"{self.s.llm_base_url}/chat/completions", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.s.llm_api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.s.llm_timeout_s) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            raise LLMUnavailable(f"ошибка вызова модели: {e}") from e
        reply = resp["choices"][0]["message"]["content"]
        tokens = (resp.get("usage") or {}).get("total_tokens") or \
            (sum(len(m.get("content", "")) for m in messages) + len(reply)) // 3
        self._record(model, tokens)
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"model": model, "messages": messages, "reply": reply, "tokens": tokens},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        return reply
