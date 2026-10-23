"""
Диагностика шлюза: как модель отвечает на короткий JSON-запрос с разными настройками.
Запуск: python3 -m llm.diag            (по умолчанию GLM; ~6 коротких запросов, доли копейки)
        python3 -m llm.diag z-ai/glm-5.3-flash google/gemma-4-26b-a4b-it
"""
import json
import sys

from .client import MODELS, raw_request

MSG = [{"role": "system", "content": "Отвечай только JSON."},
       {"role": "user", "content": 'Верни JSON {"ok": true, "city": "<столица России>"}'}]

VARIANTS = {
    "как сейчас (json_mode, 300 токенов)": {"max_tokens": 300, "response_format": {"type": "json_object"}},
    "reasoning выключен": {"max_tokens": 300, "response_format": {"type": "json_object"},
                           "reasoning": {"enabled": False, "exclude": True}},
    "reasoning low": {"max_tokens": 300, "reasoning": {"effort": "low", "exclude": True}},
    "без json_mode, 2000 токенов": {"max_tokens": 2000},
}


def main():
    models = sys.argv[1:] or [MODELS["smart"]]
    for m in models:
        for name, extra in VARIANTS.items():
            try:
                d = raw_request(m, MSG, timeout=90, **extra)
                ch = d["choices"][0]
                msg = ch.get("message") or {}
                r = msg.get("reasoning") or msg.get("reasoning_content") or ""
                print(f"{m} | {name}: finish={ch.get('finish_reason')} usage={d.get('usage')} "
                      f"content={json.dumps((msg.get('content') or '')[:120], ensure_ascii=False)} reasoning_len={len(str(r))}")
            except Exception as e:  # noqa: BLE001
                body = getattr(e, "read", lambda: b"")()[:300]
                print(f"{m} | {name}: ОШИБКА {e} {body!r}")


if __name__ == "__main__":
    main()
