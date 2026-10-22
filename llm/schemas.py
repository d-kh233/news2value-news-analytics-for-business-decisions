"""
Схемы данных для обмена между AI-компонентами (п. 6 «Пространства решений» кейса).

Главный принцип: каждое утверждение в инсайте помечено происхождением
(origin) и ссылается на источники (source_ids). Так выполняются требования:
- отделять подтверждённые факты, позицию банка и выводы модели;
- трассируемость: какие источники и признаки компании повлияли на вывод.

Идентификаторы источников (их формирует бэкенд, модель только ссылается):
  N:<id>              — новость            (N:1842)
  A:<doc>:<chunk>     — фрагмент аналитики Альфа-Банка (A:key_rate_sep26:3)
  F:<FIELD>           — признак компании из df_Uid_data_hack (F:CL_PRODUCT_LOA_MARK)
  P:<counterparty_id> — связь по платежам из df_Uid_payment_hack (P:U_000123)
  G:<group_id>        — группа компаний (G:5531)
"""
from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- Общие перечисления ----------

class Origin(str, Enum):
    fact = "fact"   # подтверждено источником (новость, данные компании)
    bank = "bank"   # позиция / аналитика Альфа-Банка
    ai = "ai"       # вывод или предположение модели


class EventType(str, Enum):
    monetary_policy = "monetary_policy"   # ставка ЦБ, ДКП
    inflation = "inflation"
    fx = "fx"                             # валютные курсы
    commodities = "commodities"           # сырьё, металлы, нефть, золото
    regulation = "regulation"             # законы, налоги, регуляторика
    sanctions = "sanctions"
    trade = "trade"                       # ВЭД, пошлины, логистика
    macro = "macro"                       # ВВП, рынок труда, потребление
    debt_market = "debt_market"           # облигации, кредитный рынок
    industry = "industry"                 # отраслевые события
    counterparty = "counterparty"         # события у контрагентов
    corporate = "corporate"               # M&A, банкротства, отчётность
    other = "other"


class ImpactType(str, Enum):
    risk = "risk"
    opportunity = "opportunity"
    mixed = "mixed"
    neutral = "neutral"


class Level(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Horizon(str, Enum):
    immediate = "immediate"   # дни
    short = "short"           # до 3 месяцев
    medium = "medium"         # 3–12 месяцев
    long = "long"             # больше года


# ---------- Шаг 1. Извлечение события из новости (Gemma) ----------

class Factor(str, Enum):
    """Макрофакторы. Совпадают с колонками карты ОКВЭД (okved/okved_sensitivity.csv),
    чтобы ранжирование могло считать: сила движения фактора × чувствительность отрасли."""
    key_rate = "key_rate"                 # ключевая ставка, стоимость заёмных денег
    usd_rub = "usd_rub"                   # курс; up = рубль слабеет
    oil_gas = "oil_gas"                   # цены на нефть, газ, энергоносители
    metals = "metals"                     # цены на металлы и руды
    agri = "agri"                         # цены на зерно, сельхозсырьё, продовольствие
    gold = "gold"
    inflation = "inflation"
    consumer_demand = "consumer_demand"   # потребительский спрос, розница
    labor_market = "labor_market"         # дефицит кадров, рост зарплат
    sanctions_trade = "sanctions_trade"   # санкции, пошлины, ВЭД, логистика импорта/экспорта


class Direction(str, Enum):
    up = "up"
    down = "down"
    unchanged = "unchanged"   # важно: «ставку сохранили» — тоже событие
    uncertain = "uncertain"


class FactorMove(BaseModel):
    factor: Factor
    direction: Direction
    strength: Level = Field(description="Насколько сильное движение / сигнал")
    evidence: str = Field(description="Короткая цитата или пересказ места в тексте, на котором основан вывод")


class KeyNumber(BaseModel):
    name: str = Field(description="Что за показатель: 'ключевая ставка', 'инфляция г/г', 'цена Brent'")
    value: str = Field(description="Значение как в тексте: '14', '7,9', '−3,5'")
    unit: Optional[str] = Field(default=None, description="'%', '% г/г', 'руб.', 'долл./барр.', 'млрд руб.'")


class Fact(BaseModel):
    text: str
    confirmed: bool = Field(description="true — прямо сказано в источнике; false — предположение/прогноз из текста")


class EventExtractionLLM(BaseModel):
    """То, что возвращает модель. news_id и метаданные добавляет код, а не LLM."""
    event_type: EventType
    title: str = Field(description="Короткий нейтральный заголовок события, до 12 слов")
    summary: str = Field(description="1–2 предложения: что произошло")
    facts: List[Fact] = Field(default_factory=list)
    actors: List[str] = Field(default_factory=list, description="Организации, регуляторы, страны")
    industries: List[str] = Field(default_factory=list, description="Затронутые отрасли простыми словами")
    okved_hints: List[str] = Field(default_factory=list, description="Коды ОКВЭД (2 знака), если очевидны")
    regions: List[str] = Field(default_factory=list)
    countries: List[str] = Field(default_factory=list)
    instruments: List[str] = Field(default_factory=list, description="Ставка, курс, товар, индекс: 'ключевая ставка', 'USD/RUB', 'медь'")
    key_numbers: List[KeyNumber] = Field(default_factory=list)
    factor_moves: List[FactorMove] = Field(default_factory=list)
    event_date: Optional[str] = Field(default=None, description="YYYY-MM-DD, если указано")
    is_noise: bool = Field(default=False, description="true — реклама, мусор, нет события")
    quality_flags: List[Literal["truncated", "no_date", "no_source", "ambiguous", "opinion_only", "duplicate_like"]] = Field(
        default_factory=list, description="Проблемы с исходным текстом (для ограничения 05 кейса)"
    )


class EventExtraction(EventExtractionLLM):
    news_id: str
    source: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[str] = None
    model: Optional[str] = None
    prompt_version: Optional[str] = None


# ---------- Шаг 2. Оценка релевантности для компании (Gemma) ----------

class MatchedFeature(BaseModel):
    source_id: str = Field(description="F:..., P:..., G:...")
    why: str = Field(description="Как этот признак связывает событие с компанией")


class RelevanceAssessment(BaseModel):
    news_id: str
    company_id: str
    relevance: int = Field(ge=0, le=100)
    channel: Literal["direct", "industry", "counterparty", "financial", "macro", "none"] = Field(
        description="Канал влияния: напрямую на компанию, через отрасль, через контрагентов, через фин. продукты/долг, через макро"
    )
    matched_features: List[MatchedFeature] = Field(default_factory=list)
    reason: str = Field(description="Одно предложение для пользователя: почему это может быть важно")


# ---------- Шаг 3. План агента ----------

ToolName = Literal[
    "get_company_profile",
    "get_counterparties",
    "search_bank_analytics",
    "search_news",
    "get_group_companies",
]


class PlanStep(BaseModel):
    tool: ToolName
    args: dict = Field(default_factory=dict)
    goal: str = Field(description="Зачем нужен этот шаг")


class AgentPlan(BaseModel):
    questions: List[str] = Field(description="Что нужно выяснить, чтобы объяснить влияние события на компанию")
    steps: List[PlanStep] = Field(max_length=6)


# ---------- Шаг 4. Персонализированный инсайт (GLM / DeepSeek) ----------

class Statement(BaseModel):
    text: str
    origin: Origin
    source_ids: List[str] = Field(default_factory=list)

    @field_validator("source_ids")
    @classmethod
    def _need_sources(cls, v, info):
        origin = info.data.get("origin")
        if origin in (Origin.fact, Origin.bank) and not v:
            raise ValueError("Утверждения fact и bank обязаны ссылаться на источник")
        return v


class Consequence(BaseModel):
    text: str
    kind: Literal["risk", "opportunity", "factor"]
    level: Level
    horizon: Horizon
    origin: Origin
    source_ids: List[str] = Field(default_factory=list)


class Action(BaseModel):
    text: str = Field(description="Конкретное направление действий или вопрос для анализа")
    type: Literal["action", "question", "scenario"]
    bank_product: Optional[str] = Field(
        default=None,
        description="Общая категория продукта банка, если уместно ('кредитование', 'хеджирование валютного риска'). Без условий и ставок.",
    )
    source_ids: List[str] = Field(default_factory=list)


class Insight(BaseModel):
    news_id: str
    company_id: str
    headline: str = Field(description="Заголовок-вывод для руководителя, до 14 слов, не пересказ новости")
    impact_type: ImpactType
    importance: Level
    confidence: Level

    what_happened: List[Statement] = Field(min_length=1, description="01 Что произошло")
    why_it_matters: List[Statement] = Field(min_length=1, description="02 Почему это важно для этой компании")
    consequences: List[Consequence] = Field(min_length=1, description="03 Какие последствия возможны")
    actions: List[Action] = Field(default_factory=list, description="04 Какие действия могут быть релевантны")
    bank_view: List[Statement] = Field(default_factory=list, description="05 Как это оценивает банк (только origin=bank)")
    company_features_used: List[str] = Field(default_factory=list, description="06 Признаки компании, повлиявшие на вывод (F:/P:/G:)")

    data_gaps: List[str] = Field(default_factory=list, description="Чего не хватило для уверенного вывода")

    @field_validator("bank_view")
    @classmethod
    def _bank_only(cls, v):
        for s in v:
            if s.origin != Origin.bank:
                raise ValueError("bank_view может содержать только origin=bank")
        return v

    def all_source_ids(self) -> set[str]:
        ids: set[str] = set(self.company_features_used)
        for block in (self.what_happened, self.why_it_matters, self.bank_view):
            for s in block:
                ids.update(s.source_ids)
        for c in self.consequences:
            ids.update(c.source_ids)
        for a in self.actions:
            ids.update(a.source_ids)
        return ids


# ---------- Шаг 5. Самопроверка (критик) ----------

class Issue(BaseModel):
    field: str = Field(description="Где проблема: 'why_it_matters[0]', 'bank_view', 'headline'...")
    problem: Literal[
        "unsupported_claim",     # нет опоры на источник
        "wrong_origin",          # ИИ-вывод выдан за позицию банка или факт
        "contradiction",
        "generic",               # общая фраза, не про эту компанию
        "retelling",             # просто пересказ новости
        "missing_bank_view",
        "invented_numbers",
        "other",
    ]
    comment: str


class CriticVerdict(BaseModel):
    verdict: Literal["pass", "revise", "need_more_data"]
    scores: dict = Field(
        description="Оценки 1–5: grounding, personalization, depth, actionability, bank_separation"
    )
    issues: List[Issue] = Field(default_factory=list)
    extra_search: List[PlanStep] = Field(default_factory=list, description="Только при need_more_data")


# ---------- Чат по инсайту ----------

class ChatAnswer(BaseModel):
    answer: str
    statements: List[Statement] = Field(default_factory=list)
    out_of_scope: bool = False
    suggested_questions: List[str] = Field(default_factory=list, max_length=3)


# ---------- Дайджест ----------

class DigestItem(BaseModel):
    news_id: str
    line: str = Field(description="Одна строка: вывод для компании")
    impact_type: ImpactType
    importance: Level


class Digest(BaseModel):
    title: str
    overview: str = Field(description="2–3 предложения: главное за период для этой компании")
    items: List[DigestItem]
