"""Настройки агента из переменных окружения. Ключ модели — только в окружении (.env), в коде его нет."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env без зависимостей. Уже заданные переменные не перезаписывает."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'"))


@dataclass
class Settings:
    # Профили для запуска агента без бэкенда (CLI, тесты). В бэкенде профиль приходит из БД / моков.
    companies_csv: str = str(DATA_DIR / "df_Uid_data_mock.csv")

    # LLM: stub (без сети, правила) | live (вызовы + запись в кэш) | replay (только кэш — режим для жюри).
    # Имена моделей — LLM_MODEL_FAST / LLM_MODEL_SMART, их читает llm/client.py.
    llm_mode: str = "stub"
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_use_smart: bool = False         # интерпретация на SMART-модели; в разработке False — бережём лимит
    llm_use_critic: bool = True         # второй вызов проверяет карточку (+1–2 вызова на карточку); в stub не работает
    llm_cache_dir: str = str(REPO_ROOT / "replay_cache" / "llm")  # коммитим: нужен жюри для replay
    llm_daily_budget_rub: float = 500.0
    llm_price_rub_per_1k_tokens: float = 0.025  # по факту 25.10: 23 ₽ по ключу при оценке 491 ₽ с ценой 0.5
    llm_timeout_s: float = 90.0

    # Агент
    top_k_news: int = 5
    min_relevance: float = 0.5          # на моках: чужие отрасли 0.3–0.45, общие макро 0.5–0.9, своя отрасль 0.75+
    theses_per_news: int = 2
    max_extract_per_run: int = 20       # сколько неразмеченных новостей агент размечает сам за прогон
    llm_parallel: int = 4               # сколько новостей размечать одновременно (LLM_PARALLEL)
    runs_dir: str = str(REPO_ROOT / "runs")
    today: str = ""                     # YYYY-MM-DD; в replay — та же дата, что при записи кэша

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(REPO_ROOT / ".env")
        e = os.environ.get
        s = cls()
        s.companies_csv = e("COMPANIES_CSV", s.companies_csv)
        s.llm_mode = e("LLM_MODE", s.llm_mode).lower()
        s.llm_base_url = e("LLM_BASE_URL", s.llm_base_url).rstrip("/")
        s.llm_api_key = e("LLM_API_KEY", s.llm_api_key)
        s.llm_use_smart = e("LLM_USE_SMART", "0").lower() in ("1", "true", "yes")
        s.llm_use_critic = e("LLM_USE_CRITIC", "1").lower() in ("1", "true", "yes")
        s.llm_cache_dir = e("LLM_CACHE_DIR", s.llm_cache_dir)
        s.llm_daily_budget_rub = float(e("LLM_DAILY_BUDGET_RUB", s.llm_daily_budget_rub))
        s.llm_price_rub_per_1k_tokens = float(e("LLM_PRICE_RUB_PER_1K", s.llm_price_rub_per_1k_tokens))
        s.top_k_news = int(e("TOP_K_NEWS", s.top_k_news))
        s.min_relevance = float(e("MIN_RELEVANCE", s.min_relevance))
        s.llm_parallel = int(e("LLM_PARALLEL", s.llm_parallel))
        s.runs_dir = e("RUNS_DIR", s.runs_dir)
        s.today = e("AGENT_TODAY", s.today)
        if s.llm_mode not in ("stub", "live", "replay"):
            raise ValueError(f"LLM_MODE={s.llm_mode!r}: допустимо stub | live | replay")
        if not Path(s.llm_cache_dir).is_absolute():
            s.llm_cache_dir = str(REPO_ROOT / s.llm_cache_dir)
        return s
