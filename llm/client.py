"""
Вызов LLM с гарантией JSON по схеме.

Как встроить в бэкенд:
  Если в бэкенде уже есть LLM-шлюз (лимит, кэш, лог), передайте его функцию в
  call_json(..., complete=gateway_fn). Сигнатура:
      complete(model: str, messages: list[dict], **params) -> str
  Если ничего не передать, используется простой клиент OpenAI-совместимого API
  ниже (для локальных проверок промптов).

Ключ берётся только из переменных окружения (.env), в коде его нет.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("llm")

def _load_dotenv(path: str = ".env") -> None:
    """Минимальная загрузка .env без лишних зависимостей. Уже заданные переменные не трогаем."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

T = TypeVar("T", bound=BaseModel)
CompleteFn = Callable[..., str]

# ---------- Модели (id из чата организаторов 22.10). Цена, руб. за 1М токенов вход/выход:
# gemma 4,48/23 · glm 5,10/17 · deepseek 10/21 · qwen3.6 5,33/74 · kimi 51/270 (не использовать) ----------

MODELS = {
    # дешёвые: классификация, извлечение, релевантность
    "fast": os.getenv("LLM_MODEL_FAST", "google/gemma-4-26b-a4b-it"),
    # интерпретация, самопроверка
    "smart": os.getenv("LLM_MODEL_SMART", "z-ai/glm-5.3-flash"),
    # поиск взаимосвязей, длинный контекст
    "research": os.getenv("LLM_MODEL_RESEARCH", "deepseek/deepseek-v4.1-flash"),
}


# ---------- Простой клиент (если нет шлюза бэкенда) ----------

# Рассуждающие модели (GLM, DeepSeek, Qwen3.6) тратят max_tokens на скрытые рассуждения и
# могут вернуть пустой content. LLM_REASONING: off | low | default — что просить у шлюза.
REASONING = os.getenv("LLM_REASONING", "off")
DEBUG_DIR = Path(os.getenv("LLM_DEBUG_DIR", ".llm_cache/raw"))


def _reasoning_params(model: str) -> dict:
    if REASONING == "default" or model.startswith("google/gemma"):
        return {}
    if REASONING == "low":
        return {"reasoning": {"effort": "low", "exclude": True}}
    return {"reasoning": {"enabled": False, "exclude": True}}


def raw_request(model: str, messages: list[dict], timeout: int = 120, **body_extra) -> dict:
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    key = os.environ["LLM_API_KEY"]
    body = {"model": model, "messages": messages, **body_extra}
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def openai_compatible_complete(model: str, messages: list[dict], temperature: float = 0.1,
                               max_tokens: int = 1500, json_mode: bool = True, timeout: int = 120) -> str:
    extra = {"temperature": temperature, "max_tokens": max_tokens, **_reasoning_params(model)}
    if json_mode and os.getenv("LLM_JSON_MODE", "1") == "1":
        extra["response_format"] = {"type": "json_object"}
    try:
        data = raw_request(model, messages, timeout=timeout, **extra)
    except urllib.error.HTTPError as e:
        if e.code != 400 or "reasoning" not in extra:
            raise
        log.warning("шлюз не принял параметр reasoning (400), повторяю без него")
        extra.pop("reasoning")
        data = raw_request(model, messages, timeout=timeout, **extra)
    choice = data["choices"][0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    usage = data.get("usage", {})
    log.info("llm model=%s in=%s out=%s finish=%s", model, usage.get("prompt_tokens"),
             usage.get("completion_tokens"), choice.get("finish_reason"))
    if "{" not in content:
        # сохраняем сырой ответ без промпта, чтобы понять, что пошло не так
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        f = DEBUG_DIR / f"{int(time.time() * 1000)}_{model.replace('/', '_')}.json"
        f.write_text(json.dumps({"finish_reason": choice.get("finish_reason"), "usage": usage,
                                 "content": content[:3000],
                                 "reasoning": str(msg.get("reasoning") or msg.get("reasoning_content") or "")[:3000]},
                                ensure_ascii=False, indent=1))
        reason = msg.get("reasoning") or msg.get("reasoning_content")
        hint = " (модель потратила токены на рассуждения)" if reason else ""
        return content + f"\n[пустой ответ{hint}, finish_reason={choice.get('finish_reason')}, см. {f}]"
    return content


# ---------- Кэш на диске (в разработке экономит лимит 500 ₽) ----------

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))


def _cache_key(model: str, messages: list[dict], params: dict) -> str:
    raw = json.dumps({"m": model, "msg": messages, "p": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[str]:
    p = CACHE_DIR / f"{key}.json"
    return json.loads(p.read_text())["content"] if p.exists() else None


def _cache_put(key: str, content: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps({"content": content}, ensure_ascii=False))


# ---------- Разбор ответа ----------

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели: убирает <think>, ```json, текст вокруг."""
    t = _THINK.sub("", text).strip()
    m = _FENCE.search(t)
    if m:
        t = m.group(1).strip()
    start = t.find("{")
    if start == -1:
        raise ValueError("В ответе нет JSON-объекта")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("JSON-объект обрезан (не хватило max_tokens?)")


# ---------- Главная функция ----------

class LLMCallError(RuntimeError):
    pass


def call_json(schema: Type[T], messages: list[dict], model: str, complete: Optional[CompleteFn] = None,
              retries: int = 1, use_cache: bool = True, trace: Optional[list] = None,
              post_check: Optional[Callable[[T], None]] = None, **params) -> T:
    """
    Вызывает модель и возвращает объект схемы. При ошибке разбора или валидации
    один раз просит модель исправить ответ, показывая ей ошибку.
    post_check(obj) — доп. проверка (например, что ссылки только на переданные источники);
    бросает ValueError с понятным текстом, который увидит модель.
    trace — список, куда пишутся шаги (для трассировки, критерий 12).
    """
    complete = complete or openai_compatible_complete
    msgs = list(messages)
    last_err = None
    for attempt in range(retries + 1):
        key = _cache_key(model, msgs, params)
        cached = _cache_get(key) if use_cache else None
        t0 = time.time()
        raw = cached if cached is not None else complete(model=model, messages=msgs, **params)
        took = round(time.time() - t0, 2)
        try:
            obj = schema.model_validate(extract_json(raw))
            if post_check is not None:
                post_check(obj)
            if use_cache and cached is None:
                _cache_put(key, raw)
            if trace is not None:
                trace.append({"schema": schema.__name__, "model": model, "attempt": attempt,
                              "cached": cached is not None, "seconds": took, "ok": True})
            return obj
        except (ValueError, ValidationError) as e:
            last_err = e
            if trace is not None:
                trace.append({"schema": schema.__name__, "model": model, "attempt": attempt,
                              "cached": cached is not None, "seconds": took, "ok": False,
                              "error": str(e)[:500]})
            log.warning("invalid JSON from %s (attempt %s): %s", model, attempt, str(e)[:300])
            msgs = msgs + [
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": (
                    "Ответ не прошёл проверку схемы. Ошибка:\n" + str(e)[:1500] +
                    "\nВерни исправленный JSON целиком, только JSON."
                )},
            ]
    raise LLMCallError(f"{schema.__name__}: модель не вернула валидный JSON: {last_err}")
