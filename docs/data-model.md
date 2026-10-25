# Модель данных News2Value

PostgreSQL 16. Таблицы создают миграции Alembic (`backend/migrations/versions/0001…0009`), ORM-модели лежат в `backend/app/models/`. Схема API — [openapi.json](openapi.json), описание для фронтенда — [api-contract.md](api-contract.md).

Сплошная линия на схеме — внешний ключ (FK: база сама следит за связью). Пунктир — логическая связь без FK, её проверяет код. Почему FK нет: в данных банка много ссылок на компании, которых нет в справочнике, а вопросы, оценки и журнал должны пережить пересоздание инсайтов при `refresh`.

```mermaid
erDiagram
    companies ||--o| company_profiles : "профиль (FK, CASCADE)"
    companies ||--o{ insights : "инсайты (FK, CASCADE)"
    companies ||--o{ pipeline_runs : "запуски refresh (FK)"
    companies ||--o{ jobs : "фоновые задачи (FK)"
    companies ||--o{ users : "пользователи компании (FK, CASCADE)"
    companies ||--o{ insight_questions : "(FK)"
    companies ||--o{ insight_feedback : "(FK)"
    pipeline_runs ||--o{ insights : "run_id (FK, SET NULL)"
    pipeline_runs ||--o{ jobs : "run_id (FK)"
    jobs ||--o| insight_questions : "job_id ask (FK, SET NULL)"
    insights }o..o{ documents : "document_ids[] (без FK)"
    insights ||..o{ insight_questions : "insight_id (без FK)"
    insights ||..o{ insight_feedback : "insight_id (без FK)"
    persons ||--o{ person_company_links : "synthetic_f"
    companies |o..o{ person_company_links : "synthetic_u (без FK, is_known_company)"
    companies |o..o{ payments : "payer / beneficiary (без FK, *_is_known)"
    users |o..o{ audit_log : "user_id (без FK)"
    companies |o..o{ audit_log : "company_id (без FK)"

    companies {
        text synthetic_u PK "U_123 — id из данных банка"
        text mainokved_code "ОКВЭД"
        text mainokved_name
        bool cl_product_loa_mark "флаги продуктов банка"
        bool fts_flag "ВЭД"
        text fts_country_name
        bigint revenue_company
        bool revenue_known
        text city
        text region
        varchar source "bank | manual"
    }
    company_profiles {
        text company_id PK,FK
        jsonb auto_data "снимок из таблиц банка"
        jsonb manual_data "ручные поля: сырьё, клиенты, цели"
    }
    persons {
        text synthetic_f PK
    }
    person_company_links {
        text synthetic_f PK
        text synthetic_u PK
        bool is_known_company
    }
    payments {
        text payer PK
        text beneficiary PK
        bigint pay_amt
        bigint pay_cnt
    }
    rejected_rows {
        int id PK
        text source_file
        text reason "почему строка CSV отклонена"
    }
    documents {
        bigint id PK "N:doc-<id> в источниках"
        varchar kind "external_news | bank_analytics"
        text source_name
        text url "уникален"
        text title
        text text
        timestamptz published_at
        jsonb tags "economy, okved:41…"
        varchar content_hash "уникален"
        varchar status "ok | empty_text | error"
    }
    insights {
        text id PK "<компания>-<новость>-<роль>"
        text company_id FK
        bigint run_id FK "запуск, который сделал инсайт"
        bigint_array document_ids
        jsonb payload "карточка API целиком"
        varchar type "risk | opportunity | event"
        int importance "0–100"
        varchar generated_by "llm | rules | fake"
        text prompt_version
    }
    pipeline_runs {
        bigint id PK
        text company_id FK
        varchar status "running | done | empty | failed"
        jsonb steps "трейс генератора"
        jsonb summary "run_id, role, llm_mode, plan, llm_usage"
        timestamptz started_at
        timestamptz finished_at
    }
    jobs {
        text id PK "job-…"
        text company_id FK
        varchar kind "refresh | ask"
        jsonb params "role, period, user_id / question_id"
        varchar status "pending | running | done | failed"
        bigint run_id FK
    }
    llm_cache {
        varchar key PK "sha256: функция, аргументы, PROMPT_VERSION"
        jsonb value
        int hits
    }
    insight_questions {
        text id PK
        text insight_id
        text company_id FK
        text user_id
        text question "до 500 символов"
        text answer
        text job_id FK
    }
    insight_feedback {
        bigint id PK
        text insight_id "уникально с user_id"
        text company_id FK
        varchar insight_type
        text user_id
        varchar rating "useful | not_useful"
    }
    users {
        bigint id PK
        text email "уникален, нижний регистр"
        text password_hash "bcrypt"
        varchar role "owner | ceo | cfo | treasurer | admin"
        text company_id FK "у admin может быть пусто"
    }
    audit_log {
        bigint id PK
        timestamptz ts
        text user_id "пусто у фоновых скриптов"
        varchar action "ml_call | login"
        text tool "agent.generate_insights…"
        text company_id
        jsonb input_refs "только id, не тексты"
        varchar status "ok | cache_hit | error | limit | failed"
        int duration_ms
        int tokens_used
        text error
    }
```

## Таблицы по модулям

| модуль | таблицы | что хранят |
|---|---|---|
| B2 данные банка | `companies`, `persons`, `person_company_links`, `payments`, `rejected_rows` | CSV кейса (или синтетика `data/synthetic/`). Повторная загрузка не создаёт дублей |
| B3 профили | `company_profiles` | автоматическая часть из таблиц банка + ручные поля (ручные главнее) |
| B4 документы | `documents` | новости RSS, PDF-аналитика банка, синтетические новости. Дубли отсекаются по `url` и `content_hash` |
| B5 инсайты | `insights`, `pipeline_runs` | карточки и журнал запусков `refresh`: шаги, статус, ошибка |
| B6 задачи и кэш | `jobs`, `llm_cache` | очередь фоновых задач на PostgreSQL (одна активная refresh-задача на компанию), кэш ответов генератора |
| B7b вопросы и оценки | `insight_questions`, `insight_feedback` | вопросы по инсайту с ответами, оценки «полезно / нет» |
| B8+B9 доступ и журнал | `users`, `audit_log`, `insights.run_id` | пользователи и роли, журнал вызовов ML и входов, ссылка инсайта на запуск для `GET /insights/{id}/trace` |

## Важные правила

- `refresh` в одной транзакции удаляет все инсайты компании и вставляет новые. Поэтому у вопросов, оценок и журнала нет FK на `insights`.
- Удаление компании (`DELETE /companies/{id}`, только `source=manual`) удаляет её профиль, инсайты и пользователей. Журнал `audit_log` остаётся.
- В `audit_log` и логах нет текстов новостей, профилей и вопросов — только id (см. [SECURITY.md](SECURITY.md)).
