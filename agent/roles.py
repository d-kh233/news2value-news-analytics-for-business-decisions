"""Роли читателя. Список и тексты фокуса — из llm/insight.py (промпт-инженер), здесь только
веса ранжирования: роль меняет не только текст карточки, но и то, какие новости попадут в ленту.

Каждая роль — отдельный вызов модели и отдельная запись в кэше: для жюри записываем
демо-профили во всех ролях, которые покажем.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llm.insight import DEFAULT_ROLE, ROLES as ROLE_FOCUS

# множители: фактор (llm.schemas.Factor) и тип события (llm.schemas.EventType)
_FACTOR_W: dict[str, dict[str, float]] = {
    "owner": {},
    "ceo": {"consumer_demand": 1.2},
    "cfo": {"key_rate": 1.3, "usd_rub": 1.3, "inflation": 1.1},
    "treasurer": {"key_rate": 1.3, "usd_rub": 1.4},
    "accountant": {"sanctions_trade": 1.3, "labor_market": 1.1},
    "procurement": {"usd_rub": 1.2, "oil_gas": 1.3, "metals": 1.3, "agri": 1.3, "sanctions_trade": 1.2},
}
_EVENT_W: dict[str, dict[str, float]] = {
    "owner": {"corporate": 1.3, "counterparty": 1.2, "sanctions": 1.2, "regulation": 1.1},
    "ceo": {"industry": 1.2, "corporate": 1.2, "counterparty": 1.2, "macro": 1.1},
    "cfo": {"monetary_policy": 1.2, "debt_market": 1.3, "fx": 1.2},
    "treasurer": {"monetary_policy": 1.3, "debt_market": 1.3, "fx": 1.3},
    "accountant": {"regulation": 1.6, "sanctions": 1.2, "trade": 1.2,
                   "monetary_policy": 0.8, "commodities": 0.7, "fx": 0.9},
    "procurement": {"commodities": 1.3, "trade": 1.3, "sanctions": 1.2, "counterparty": 1.2, "fx": 1.1},
}


# множители веса сущностей компании (entities.py): бухгалтеру важны экспорт/импорт и взносы,
# закупкам — сырьё и поставщики, финансам — валюты обязательств
_TERM_W: dict[str, dict[str, float]] = {
    "accountant": {"F:FTS_FLAG": 2.0, "F:CL_PRODUCT_ZPP_MARK": 2.0},
    "procurement": {"F:RAW_MATERIALS": 1.2, "F:KEY_SUPPLIERS": 1.2},
    "cfo": {"F:CURRENCY_RISKS": 1.2},
    "treasurer": {"F:CURRENCY_RISKS": 1.2},
    "owner": {"F:KEY_CLIENTS": 1.2},
}


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    focus: str
    factor_weight: dict[str, float] = field(default_factory=dict)
    event_weight: dict[str, float] = field(default_factory=dict)
    term_weight: dict[str, float] = field(default_factory=dict)


ROLES: dict[str, Role] = {}
for _k, _txt in ROLE_FOCUS.items():
    _title, _, _focus = _txt.partition(":")
    ROLES[_k] = Role(_k, _title.strip(), _focus.strip(), _FACTOR_W.get(_k, {}), _EVENT_W.get(_k, {}),
                     _TERM_W.get(_k, {}))


def get_role(key: str | None) -> Role:
    k = (key or DEFAULT_ROLE).lower()
    if k not in ROLES:
        raise ValueError(f"role={key!r}: допустимо {', '.join(ROLES)}")
    return ROLES[k]
