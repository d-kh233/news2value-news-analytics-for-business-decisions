# Стык бэкенда с агентом (B5): что сделано и где расхождения

Обновлено 25.10.2026. Код агента (`agent/`) и LLM-части (`llm/`) бэкенд не меняет. Всё, что нужно поправить там, собрано в разделе «Расхождения» для тимлида и промпт-инженера.

## Как бэкенд вызывает агента

```
scripts/run_pipeline.py --company U_10 --role cfo --period week
  └─ services/refresh.py  run_for_company(company_id, role, period)
       ├─ pipeline_runs: запись со status=running
       ├─ ML_MODE=real → ml_ports/agent_port.generate_insights(company_id, role, period=...)
       │                    └─ agent.Orchestrator(news_provider=… по NEWS_SOURCE).run(...)
       │  ML_MODE=fake → ml_ports/fake_port.generate_insights(...)   (agent/ и llm/ не нужны)
       ├─ проверка карточек схемой Insight, ссылки N:doc-<id> → document_ids
       ├─ одна транзакция: удалить старые инсайты компании, вставить новые
       └─ pipeline_runs: status, steps = trace агента как есть, error
```

- **Период.** API принимает `day | week`, агент и `news_provider` — число дней. Перевод только в `agent_port.period_to_days`: day → 1, week → 7. Если period не задан — 30 дней, как было.
- **Профиль компании из БД** — строка `companies` плюс `company_profiles.manual_data` в ключе `manual_data`. Агент сам переводит её ключи в верхний регистр (`CompanyProfile.from_row`). Ручные поля заполняют только пустые банковские. Так сырьё, клиенты и цели из демо-профилей доходят до поиска сущностей агента.
- **Новости.** `NEWS_SOURCE=db` → `news_port.news_provider(company_id, period_days)`: `external_news` со статусом ok, свежие первыми, не больше 100. `NEWS_SOURCE=mock` → агент берёт свои `agent/data/mock_news.json`.
- **Режим модели.** `LLM_MODE` (stub / live / replay) и прочие `LLM_*` агент читает из окружения сам. Бэкенд их только передаёт.

## Статусы запуска (`pipeline_runs.status`)

| статус | что было | инсайты |
|---|---|---|
| `done` | генератор вернул карточки, они сохранены | старые заменены новыми |
| `empty` | генератор отработал без ошибки, но карточек 0 | старые остались; причина и режимы — в `error` |
| `failed` | ошибка генератора, проверки схемы или сохранения | старые остались; текст ошибки — в `error` |

`steps` — трейс генератора без изменений (`step, name, tool, input, output, status, duration_ms, note`).

## Проверка на настоящих данных (25.10, LLM_MODE=stub)

`scripts/check_ml_contract.py` на U_1 (46.90) и U_10 (63.11): сигнатура, схема карточек и сводки — **контракт соблюдён** при обоих NEWS_SOURCE.

| NEWS_SOURCE | новостей найдено | карточек | document_ids |
|---|---|---|---|
| mock | 10 (уже размечены) | 2 | нет — моковые новости не строки `documents` |
| db | 60 (из `documents`) | **0** | — |

**Поэтому по умолчанию остаётся `NEWS_SOURCE=mock`.** Включать `db` можно, когда агент будет получать размеченные новости (расхождение 1).

## Расхождения

1. **Настоящие новости не размечены, и в stub/replay лента пустая.**
   - Что видим: у новости без `event` скор 0 (`agent/scoring.py`, `score_news`), `_extract_missing` размечает только в `live`. Запасной вариант с макроновостями тоже требует разметки. В `replay` записей нет: `replay_cache/llm/` пуст. В `pipeline_runs` это видно как `empty` с шагом «Разметка новостей» в статусе `skipped`.
   - Что нужно: один прогон `LLM_MODE=live` на демо-компаниях (с ключом) и коммит `replay_cache/`. Перед коммитом проверить, можно ли публиковать кэш: в промптах есть строки компаний из кейса (agent/README, открытый вопрос 4). **Не решено, ждёт тимлида.** Учтите расход: после слияния критика это 1 вызов на разметку и 2–3 на карточку; `LLM_USE_CRITIC=0` экономит.
2. **У источников в карточке нет id документа.** `llm/backend_adapter.to_backend_insight` не заполняет `sources[].id` (поле в схеме бэкенда есть). Бэкенд достаёт `document_ids` из ссылок `N:doc-<id>` в `details`: блоки, `news_id`, связанные новости повторного поиска. Работает, но надёжнее, если адаптер положит `id` в `sources`.
3. **Нет значения `generated_by` для заглушки.** Схема Insight допускает только `llm | rules`. У `ML_MODE=fake` в `payload` стоит `rules`, `details = None`, `prompt_version = fake@v1`, а в колонке `insights.generated_by` — `fake`. Схему не меняли (согласовано 23.10).
4. **Разные «сегодня».** Агент берёт `date.today()` (местная дата), `news_provider` — дату UTC. Около полуночи окна расходятся на день. На демо и в replay задавайте `AGENT_TODAY`.
5. **Окно поиска расширяется само.** Если за период новостей нет, `Orchestrator._retrieve` удваивает окно до 90 дней. `period=day` может дать новости за неделю и больше. Фактический период — в шаге «Поиск новостей» (`input.period`).
6. **У тезисов банка нет URL.** Все 20 тезисов в `agent/data/bank_theses.json` без ссылки, поэтому `sources[].url` у аналитики банка пустой. Схема пустую строку принимает.
7. **Контрагенты из платежей не доходят до агента.** `auto_data` профиля (`top_buyers`, `top_suppliers`, группа) в строку не попадает: агент читает только `KEY_CLIENTS / KEY_SUPPLIERS` из ручной части. Если они нужны, сначала договориться о формате с тимлидом.

## Что бэкенд не делал (по договорённости)

- Не трогал `agent/` и `llm/`, не вызывал модель напрямую.
- Эндпоинты инсайтов (B1) по-прежнему на моках (`mock_store`), переход на таблицу `insights` — в B7. `POST /companies/{id}/insights/refresh` с B6 — фоновая задача: таблица `jobs`, выполняет сервис `worker` (`services/job_service.py` → `refresh.run_for_company`), карточки пишутся в `insights`.
