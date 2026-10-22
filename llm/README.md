# llm/ — промпты, схемы и вызов моделей

Зона Prompt Engineer. Готово на чт 22.10: классификация и извлечение (Gemma), JSON-схемы, черновик карты ОКВЭД.

## Что здесь

| Файл | Что это | Кому |
|---|---|---|
| `schemas.py` | Контракт между шагами агента (pydantic). У каждого утверждения есть `origin` (fact / bank / ai) и `source_ids` | всем |
| `schemas_json/*.json` | Те же схемы в JSON Schema (`python -m llm.export_schemas`) | бэк, фронт |
| `prompts/extract_event.system.md` | Системный промпт извлечения события | — |
| `prompts/extract_event_examples.py` | 3 few-shot примера: ставка ЦБ, медь, реклама (шум) | — |
| `extract.py` | `extract_event(news)` и `extract_many(news_list)` | бэк |
| `client.py` | `call_json(schema, messages, model)` — вызов + разбор JSON + одна попытка исправления + кэш + трассировка | бэк, тимлид |
| `okved/okved_sensitivity.csv` | Черновик карты «ОКВЭД (2 знака) → чувствительность к 10 макрофакторам», 88 строк | аналитик (проверка), тимлид |
| `okved/sensitivity.py` | `macro_impact(event.factor_moves, company_row)` → score 0–100, знак, объяснение с `F:`-источниками | тимлид (ранжирование) |
| `okved/build_okved_map_glm.py` | Прогон карты через GLM и список расхождений с черновиком для аналитика | я + тимлид, когда есть ключ |
| `tests/test_offline.py` | 11 тестов без обращения к API | — |

## Интерпретация (generate_insight) — черновик v0

```python
from llm.insight import generate_insight
out = generate_insight(news, company_row, bank_chunks=chunks, counterparties=cps, role="cfo",
                       relevance_score=score, resolve_source=resolve, complete=gateway_complete)
```

- `company_row` — строка из таблицы `companies` (ключи как в CSV, регистр любой) или профиль с `manual_data`.
- `bank_chunks` — найденные фрагменты аналитики банка `[{id: "A:doc:chunk", title, date, published_at, url, text}]`.
- `counterparties` — топ контрагентов `[{id, direction, okved_name, share_pct}]` (необязательно).
- `role` — owner / ceo / cfo / treasurer / accountant / procurement: меняет акценты, факты те же.
- Возвращает dict ровно по схеме backend `Insight` + необязательные `details`, `used_profile_fields`, `prompt_version`, `generated_by`, `role`.
- Модель видит список допустимых id источников. Если сослалась на несуществующий или не связала вывод с признаками компании — ей показывают ошибку и просят исправить.
- Промпт: `prompts/insight.system.md`. Версия: `PROMPT_VERSION` в `backend_adapter.py`.

## Как встроить в бэкенд (Даша)

```python
from llm.extract import extract_event

ev = extract_event(news_dict, complete=gateway_complete)  # gateway_complete — ваш LLM-шлюз
```

`news_dict`: `{"id", "title", "text", "source", "url", "published_at"}`.
`gateway_complete(model, messages, **params) -> str` — функция шлюза (лимит, кэш, лог). Если её не передать, используется простой OpenAI-совместимый клиент из `client.py`.

Результат — `EventExtraction`: тип события, факты (подтверждено / прогноз), отрасли, подсказки ОКВЭД, регионы, страны, ключевые числа, **движения макрофакторов** (`factor_moves`), флаги шума и качества текста. `is_noise=True` — новость дальше не обрабатываем.

Модель вызывается с `temperature=0`, few-shot из 3 примеров. Если ответ не прошёл схему, модели показывают ошибку и просят исправить (один раз), потом `LLMCallError`. Пачкой (`extract_many`) плохие новости не роняют обработку, а уходят в список ошибок.

## Как связано с ранжированием (тимлид)

`factor_moves` из новости × карта ОКВЭД × признаки компании (кредиты, лизинг, ВЭД, депозиты, зарплатный проект) = `macro_impact(...)`. Это один из признаков скоринга рядом с близостью эмбеддингов, сущностями и свежестью. Поле `why` сразу идёт в промпт интерпретации и в панель трассировки.

Примеры (из тестов): повышение ставки бьёт по застройщику с кредитом сильнее, чем по юрфирме; слабый рубль — возможность для металлурга-экспортёра и риск для импортёра-оптовика; «ставку сохранили» — значимо для закредитованных, но без знака.

## Источники (`source_ids`)

`N:<id>` новость · `A:<doc>:<chunk>` аналитика банка · `F:<FIELD>` признак компании · `P:<id>` контрагент по платежам · `G:<id>` группа. Идентификаторы формирует код, модель только ссылается на них. Утверждение с `origin=bank` или `fact` без источника не пройдёт валидацию.

## Ключ

Только в `.env` (шаблон — `.env.example`). Точные id моделей и адрес API — из письма организаторов. Перед первым прогоном проверить, поддерживает ли API `response_format`; если нет — `LLM_JSON_MODE=0`.

## Проверка

```bash
python -m unittest llm.tests.test_offline -v     # без API
python -m llm.extract my_news.json               # на своих новостях, тратит лимит Gemma
python -m llm.okved.build_okved_map_glm          # ~8 запросов к GLM
```

## Что дальше по плану

- Пт: промпты интерпретации (6 блоков, схема `Insight` уже есть), разделение банк / факт / ИИ, тестовый набор 15–20 новостей × 5–6 профилей.
- Сб: самопроверка (`CriticVerdict`) и повторный поиск, тесты на шуме, метрики.
