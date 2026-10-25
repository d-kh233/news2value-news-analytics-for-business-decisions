"""Синтетические данные для сдачи (B8+B9): весь сценарий работает без data/raw и без интернета.

Запуск из backend/ (результат идёт в git, повторный запуск даёт те же файлы — seed фиксирован):
    uv run python scripts/make_synthetic.py
    uv run python scripts/make_synthetic.py --seed 42 --out ../data/synthetic

Что создаётся в data/synthetic/ (формат — как у CSV банка: разделитель «;», те же колонки):
  • df_Uid_data_hack.csv — 30 компаний: три демо-компании (U_67064 стройка, U_19717 АЗС,
    U_16346 интернет-торговля — к ним есть ручные профили в data/demo_profiles/) и 27 других
    отраслей: поставщики и покупатели демо-компаний, промышленность, торговля, услуги;
  • df_Fid_Uid_link_hack.csv — связи физлиц с компаниями (у части людей несколько компаний);
  • df_Uid_payment_hack.csv — платежи между компаниями (у демо-компаний — по сюжету: стройка
    платит за цемент и металл, АЗС — оптовику топлива, магазин — складу и перевозчику) и
    несколько платежей неизвестным компаниям, как в настоящих данных;
  • news.json — 13 выдуманных новостей (ставка, валюта, сырьё, регулирование) с пометкой [ДЕМО].
    Вместо даты — days_ago: seed_demo.py --source synthetic ставит дату «сегодня минус N дней»,
    чтобы окно агента (неделя) не опустело со временем.

Все значения выдуманы. Из data/raw ничего не читается и не копируется: совпадают только id
демо-компаний (это синтетические id кейса) и общедоступные коды и названия ОКВЭД.
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"
SEED = 42

COMPANY_COLUMNS = [
    "SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL",
    "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK", "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION", "CL_TYPE", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK", "REVENUE_COMPANY", "GROUP_ID", "CITY",
    "REGION",
]  # fmt: skip
LINK_COLUMNS = ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"]
PAYMENT_COLUMNS = ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]

# (город, регион, региональный центр банка)
CITIES = [
    ("г. Москва", "Москва", "МОСКОВСКИЙ"),
    ("г. Санкт-Петербург", "Санкт-Петербург", "Санкт-Петербург"),
    ("г. Екатеринбург", "Свердловская область", "УРАЛЬСКИЙ"),
    ("г. Новосибирск", "Новосибирская область", "СИБИРСКИЙ"),
    ("г. Казань", "Республика Татарстан", "ПРИВОЛЖСКИЙ"),
    ("г. Краснодар", "Краснодарский край", "ЮЖНЫЙ"),
    ("г. Челябинск", "Челябинская область", "УРАЛЬСКИЙ"),
]
AGES = ["0-3", "4-7", "8-15", "16-25", "26+"]
ECO = ["Рождение", "Рост", "Оптимизация", "Зрелость", ""]
WORKERS = ["Менее 15", "16-100", "101-250", "251-1000", "Более 1000"]
CL_TYPES = ["CLIENT", "CLOSE_CLIENT", "PROSPECT"]
COUNTRIES = ["КИТАЙ", "ТУРЦИЯ", "КАЗАХСТАН", "БЕЛАРУСЬ", "ИНДИЯ", "ОАЭ"]

# демо-компании: id, ОКВЭД, название ОКВЭД, город (индекс CITIES), ВЭД-страны
DEMO = [
    ("U_67064", "41.20", "Строительство жилых и нежилых зданий", 0, ""),
    ("U_19717", "47.30", "Торговля розничная моторным топливом в специализированных магазинах",
     5, ""),
    ("U_16346", "47.91",
     "Торговля розничная по почте или по информационно-коммуникационной сети Интернет", 1,
     "КИТАЙ"),
]  # fmt: skip
# остальные: ОКВЭД, название (общедоступный классификатор ОК 029-2014), роль в сюжете
OTHERS = [
    ("23.51", "Производство цемента", "supplier:U_67064"),
    ("24.10", "Производство чугуна, стали и ферросплавов", "supplier:U_67064"),
    ("23.32", "Производство кирпича, черепицы и прочих строительных изделий из обожженной глины",
     "supplier:U_67064"),
    ("43.21", "Производство электромонтажных работ", "supplier:U_67064"),
    ("68.10", "Покупка и продажа собственного недвижимого имущества", "buyer:U_67064"),
    ("46.71", "Торговля оптовая твердым, жидким и газообразным топливом и подобными продуктами",
     "supplier:U_19717"),
    ("19.20", "Производство нефтепродуктов", "supplier:U_19717"),
    ("49.41", "Деятельность автомобильного грузового транспорта", "buyer:U_19717"),
    ("49.41", "Деятельность автомобильного грузового транспорта", "supplier:U_16346"),
    ("52.10", "Деятельность по складированию и хранению", "supplier:U_16346"),
    ("46.90", "Торговля оптовая неспециализированная", "supplier:U_16346"),
    ("63.11", "Деятельность по обработке данных, предоставление услуг по размещению информации",
     "supplier:U_16346"),
    ("10.71", "Производство хлеба и мучных кондитерских изделий", ""),
    ("01.11", "Выращивание зерновых культур", ""),
    ("20.15", "Производство удобрений и азотных соединений", ""),
    ("46.34", "Торговля оптовая напитками", ""),
    ("62.01", "Разработка компьютерного программного обеспечения", ""),
    ("35.11", "Производство электроэнергии", ""),
    ("55.10", "Деятельность гостиниц и прочих мест для временного проживания", ""),
    ("86.10", "Деятельность больничных организаций", ""),
    ("16.10", "Распиловка и строгание древесины", ""),
    ("29.32", "Производство прочих комплектующих и принадлежностей для автотранспортных средств",
     ""),
    ("47.11", "Торговля розничная преимущественно пищевыми продуктами в неспециализированных "
     "магазинах", ""),
    ("68.20", "Аренда и управление собственным или арендованным недвижимым имуществом", ""),
    ("45.20", "Техническое обслуживание и ремонт автотранспортных средств", "buyer:U_19717"),
    ("25.11", "Производство строительных металлических конструкций", "supplier:U_67064"),
    ("47.19", "Торговля розничная прочая в неспециализированных магазинах", ""),
]  # fmt: skip
FIRST_ID = 9_000_001  # id вне диапазона id кейса, кроме трёх демо-компаний

# Выдуманные новости для демо без интернета. Темы: ставка, валюта, сырьё, регулирование
NEWS = [
    ("S01", 1, "Банк России сохранил ключевую ставку на уровне 16%",
     "Совет директоров Банка России сохранил ключевую ставку на уровне 16% годовых. Регулятор "
     "отметил, что инфляция замедляется медленнее прогноза, и допустил паузу до конца года. "
     "Кредиты для бизнеса и проектное финансирование застройщиков остаются дорогими.",
     ["economy", "key_rate"]),
    ("S02", 2, "Курс юаня вырос до 11,8 рубля на Московской бирже",
     "Курс юаня на Московской бирже вырос до 11,8 рубля. Аналитики связывают ослабление рубля "
     "со снижением экспортной выручки и ростом импорта. Импортёры товаров из Китая, в том числе "
     "интернет-магазины, пересчитывают закупочные цены.",
     ["economy", "fx"]),
    ("S03", 2, "Цемент подорожал на 9% с начала осени",
     "Производители цемента повысили отпускные цены на 9% с начала сентября. Рост цен на "
     "стройматериалы связан с подорожанием энергоносителей и логистики. Застройщики жилья "
     "предупреждают о росте себестоимости строительства жилых комплексов.",
     ["economy", "commodities"]),
    ("S04", 3, "Биржевые цены на бензин Аи-95 обновили рекорд",
     "Биржевые цены на бензин Аи-95 выросли до рекордных значений на фоне ремонтов НПЗ. "
     "Независимые сети АЗС жалуются на сокращение маржи: розничные цены на топливо растут "
     "медленнее оптовых. Минэнерго обсуждает ограничение экспорта бензина.",
     ["economy", "commodities"]),
    ("S05", 3, "Правительство продлило ограничение экспорта бензина до конца года",
     "Правительство продлило временный запрет на экспорт бензина для производителей до 31 "
     "декабря. Мера должна снизить оптовые цены на топливо на внутреннем рынке. Для сетей АЗС "
     "это может означать стабилизацию закупочных цен.",
     ["economy", "regulation"]),
    ("S06", 4, "Металлопрокат подешевел на 6% за месяц",
     "Цены на арматуру и металлопрокат снизились на 6% за месяц из-за слабого спроса со стороны "
     "строительства. Металлурги сокращают выпуск стали. Для застройщиков это частично "
     "компенсирует рост цен на цемент и кирпич.",
     ["economy", "commodities"]),
    ("S07", 4, "Условия льготной ипотеки изменят с 1 января",
     "Минфин подготовил изменения в программу льготной ипотеки: с 1 января ставка будет зависеть "
     "от числа детей в семье. Застройщики ожидают снижения продаж новостроек в первом квартале "
     "и просят сохранить субсидирование для крупных городов.",
     ["economy", "regulation"]),
    ("S08", 5, "Госдума приняла закон о платформенной экономике",
     "Госдума приняла закон о платформенной экономике: маркетплейсы обязаны раскрывать продавцам "
     "правила ранжирования и не могут менять условия договора в одностороннем порядке. "
     "Интернет-торговля и продавцы на маркетплейсах получат больше защиты.",
     ["economy", "regulation"]),
    ("S09", 5, "Ставки по кредитам для бизнеса снизились впервые за полгода",
     "Средняя ставка по кредитам для малого и среднего бизнеса снизилась впервые за полгода. "
     "Банки ожидают смягчения денежно-кредитной политики в следующем году. Спрос на кредиты на "
     "оборотные средства остаётся высоким.",
     ["economy", "key_rate"]),
    ("S10", 6, "Тарифы на грузовые автоперевозки выросли на 12%",
     "Стоимость грузовых автоперевозок по России выросла на 12% с начала года из-за дефицита "
     "водителей и подорожания дизтоплива. Логисты и интернет-магазины пересматривают тарифы "
     "доставки до пунктов выдачи заказов.",
     ["economy", "logistics"]),
    ("S11", 6, "Инфляция в годовом выражении замедлилась до 7,1%",
     "Годовая инфляция в октябре замедлилась до 7,1%. Быстрее всего дорожали услуги и "
     "стройматериалы, продовольственные товары подорожали меньше. Потребительский спрос "
     "остаётся высоким.",
     ["economy", "inflation"]),
    ("S12", 7, "Увеличены пошлины на импорт отдельных товаров из Китая",
     "Правительство повысило ввозные пошлины на отдельные категории потребительских товаров из "
     "Китая. Мера затронет продавцов электроники и бытовых товаров, в том числе интернет-магазины, "
     "закупающие товар напрямую у китайских поставщиков.",
     ["economy", "trade"]),
    ("S13", 7, "Нефть Brent подешевела до 68 долларов за баррель",
     "Цена нефти Brent снизилась до 68 долларов за баррель на ожиданиях роста добычи. Снижение "
     "экспортных цен на нефть давит на рубль и бюджетные доходы, но может замедлить рост "
     "оптовых цен на топливо.",
     ["economy", "commodities"]),
]  # fmt: skip


def _flag(rng: random.Random, p: float) -> str:
    return "1.0" if rng.random() < p else "0.0"


def _company(rng: random.Random, cid: str, okved: str, name: str, city: int, fts: str) -> dict:
    city_name, region, _ = CITIES[city]
    revenue = rng.choice([0.0, 1000.0]) if rng.random() < 0.08 else rng.randint(5, 9000) * 1e6
    return {
        "SYNTHETIC_U": cid,
        "ORG_TYPE": "КОММ_ОРГ",
        "OKFS_NAME": "ЧАСТНАЯ СОБСТВЕННОСТЬ",
        "COMPANY_AGE_YEARS_GRADATION": rng.choice(AGES),
        "ECO_MARK_LABEL": rng.choice(ECO),
        "MAINOKVED_CODE": okved,
        "MAINOKVED_NAME": name,
        "NEGATIVEMARK": _flag(rng, 0.1),
        "AMOUNT_WORKERS_FNS_GRADATION": rng.choice(WORKERS),
        "AMOUNT_WORKERS_SPARK_GRADATION": rng.choice([*WORKERS, ""]),
        "CL_TYPE": rng.choice(CL_TYPES),
        "CL_PRODUCT_RKO_MARK": _flag(rng, 0.8),
        "CL_PRODUCT_GAR_MARK": _flag(rng, 0.3),
        "CL_PRODUCT_LOA_MARK": _flag(rng, 0.5),
        "CL_PRODUCT_LEA_MARK": _flag(rng, 0.2),
        "CL_PRODUCT_DEP_MARK": _flag(rng, 0.4),
        "CL_PRODUCT_ZPP_MARK": _flag(rng, 0.5),
        "FTS_FLAG": "1.0" if fts else "0.0",
        "FTS_COUNTRY_NAME": fts,
        "FACTORING_MARK": _flag(rng, 0.15),
        "REVENUE_COMPANY": "Не определено" if revenue == 0.0 else f"{revenue:.1f}",
        "GROUP_ID": f"group_{rng.randint(1, 6)}_sb",
        "CITY": city_name,
        "REGION": region,
    }


def make_companies(rng: random.Random) -> tuple[list[dict], dict[str, str]]:
    """Компании и сюжетные роли: id -> 'supplier:U_…' / 'buyer:U_…' / ''."""
    rows, roles = [], {}
    for cid, okved, name, city, fts in DEMO:
        row = _company(rng, cid, okved, name, city, fts)
        # демо-компании — крупные клиенты банка с кредитами (по сюжету демо-профилей)
        revenue = rng.randint(2000, 9000) * 1e6
        row.update(
            CL_TYPE="CLOSE_CLIENT",
            CL_PRODUCT_RKO_MARK="1.0",
            CL_PRODUCT_LOA_MARK="1.0",
            NEGATIVEMARK="0.0",
            REVENUE_COMPANY=f"{revenue:.1f}",
        )
        rows.append(row)
        roles[cid] = ""
    for i, (okved, name, role) in enumerate(OTHERS):
        cid = f"U_{FIRST_ID + i}"
        fts = ", ".join(rng.sample(COUNTRIES, rng.randint(1, 2))) if rng.random() < 0.25 else ""
        rows.append(_company(rng, cid, okved, name, rng.randrange(len(CITIES)), fts))
        roles[cid] = role
    return rows, roles


def make_links(rng: random.Random, companies: list[dict]) -> list[dict]:
    ids = [c["SYNTHETIC_U"] for c in companies]
    rows = []
    for n in range(45):
        person = f"F_{FIRST_ID + n}"
        city_name, _, rc = rng.choice(CITIES)
        for cid in rng.sample(ids, 2 if rng.random() < 0.3 else 1):
            rows.append({"SYNTHETIC_F": person, "CITY_NAME": city_name.removeprefix("г. "),
                         "RC_NAME": rc, "SYNTHETIC_U": cid})  # fmt: skip
    return rows


def make_payments(rng: random.Random, roles: dict[str, str]) -> list[dict]:
    rows = []

    def pay(beneficiary: str, payer: str, scale: int) -> None:
        amount = rng.randint(1, 50) * scale * 1000
        count = rng.randint(1, 400)
        rows.append(
            {"BENEFICIARY": beneficiary, "PAYER": payer, "PAY_AMT": f"{float(amount):.1f}",
             "PAY_CNT": str(count)}
        )  # fmt: skip

    # сюжет: демо-компания платит поставщикам, покупатели платят ей
    for cid, role in roles.items():
        if role.startswith("supplier:"):
            pay(cid, role.split(":")[1], 10_000)
        elif role.startswith("buyer:"):
            pay(role.split(":")[1], cid, 5_000)
    ids = list(roles)
    for _ in range(90):
        a, b = rng.sample(ids, 2)
        pay(a, b, rng.choice([100, 1000, 5000]))
    # платежи компаниям, которых нет в справочнике (как в данных кейса)
    for n in range(8):
        pay(f"U_{FIRST_ID + 900 + n}", rng.choice(ids), 500)
    return rows


def make_news() -> list[dict]:
    return [
        {
            "id": nid,
            "days_ago": days,
            "title": f"[ДЕМО] {title}",
            "text": text,
            "url": f"synthetic://news/{nid}",
            "source": "synthetic",
            "tags": ["synthetic", *tags],
        }
        for nid, days, title, text, tags in NEWS
    ]


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, delimiter=";", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Синтетические данные для сдачи")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    companies, roles = make_companies(rng)
    links = make_links(rng, companies)
    payments = make_payments(rng, roles)
    news = make_news()
    _write_csv(args.out / "df_Uid_data_hack.csv", COMPANY_COLUMNS, companies)
    _write_csv(args.out / "df_Fid_Uid_link_hack.csv", LINK_COLUMNS, links)
    _write_csv(args.out / "df_Uid_payment_hack.csv", PAYMENT_COLUMNS, payments)
    (args.out / "news.json").write_text(
        json.dumps(news, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        f"{args.out}: компаний {len(companies)}, связей {len(links)}, платежей {len(payments)}, "
        f"новостей {len(news)} (seed {args.seed})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
