"""Загрузка CSV банка (юрлица, связи физлиц, платежи) в PostgreSQL.

Порядок: компании -> связи и физлица -> платежи (флаги «компания известна»
считаются по уже загруженным компаниям). Вся загрузка — одна транзакция:
при ошибке в БД не остаётся половины данных.

Правила очистки:
- пробелы по краям убираются, пустое значение -> NULL;
- строка с неверным числом полей -> rejected_rows (bad_format);
- пустой ключ -> rejected_rows (missing_id);
- полностью одинаковые строки: первая остаётся, остальные -> exact_duplicate;
- компании и связи: один ключ с разными данными -> все такие строки ambiguous_id;
- платежи с компанией, отклонённой как ambiguous_id (под одним id разные компании,
  как в выгрузке банка), не загружаются и не складываются -> ambiguous_company;
- платежи: повторы пары PAYER + BENEFICIARY складываются (сумма и количество).

Таблицы и rejected_rows после загрузки соответствуют последним файлам: строки,
которых в файле больше нет, удаляются. Исключение — компании source='manual'
(созданы через POST /companies): синхронизация их не обновляет и не удаляет.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine

from app.models import Company, Payment, Person, PersonCompanyLink
from app.models.base import Base
from app.repositories import bank_import_repo as repo
from app.repositories.bank_import_repo import CsvSpec

COMPANIES_SPEC = CsvSpec(
    staging="stg_companies",
    csv_columns=(
        "SYNTHETIC_U",
        "ORG_TYPE",
        "OKFS_NAME",
        "COMPANY_AGE_YEARS_GRADATION",
        "ECO_MARK_LABEL",
        "MAINOKVED_CODE",
        "MAINOKVED_NAME",
        "NEGATIVEMARK",
        "AMOUNT_WORKERS_FNS_GRADATION",
        "AMOUNT_WORKERS_SPARK_GRADATION",
        "CL_TYPE",
        "CL_PRODUCT_RKO_MARK",
        "CL_PRODUCT_GAR_MARK",
        "CL_PRODUCT_LOA_MARK",
        "CL_PRODUCT_LEA_MARK",
        "CL_PRODUCT_DEP_MARK",
        "CL_PRODUCT_ZPP_MARK",
        "FTS_FLAG",
        "FTS_COUNTRY_NAME",
        "FACTORING_MARK",
        "REVENUE_COMPANY",
        "GROUP_ID",
        "CITY",
        "REGION",
    ),  # fmt: skip
    key=("synthetic_u",),
    bool_columns=(
        "negativemark",
        "cl_product_rko_mark",
        "cl_product_gar_mark",
        "cl_product_loa_mark",
        "cl_product_lea_mark",
        "cl_product_dep_mark",
        "cl_product_zpp_mark",
        "fts_flag",
        "factoring_mark",
    ),  # fmt: skip
    number_columns=("revenue_company",),
)
LINKS_SPEC = CsvSpec(
    staging="stg_links",
    csv_columns=("SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"),
    key=("synthetic_f", "synthetic_u"),
)
PAYMENTS_SPEC = CsvSpec(
    staging="stg_payments",
    csv_columns=("BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"),
    key=("payer", "beneficiary"),
    number_columns=("pay_amt", "pay_cnt"),
)

DEFAULT_CHUNK_SIZE = 50_000
# выручка ровно 1000 похожа на заглушку — считаем такие строки в отчёте
SUSPICIOUS_REVENUE = 1000


@dataclass
class FileReport:
    source_file: str
    table: str
    read: int = 0  # строк данных в файле (без заголовка)
    loaded: int = 0  # записей, записанных в таблицу (вставлено или обновлено)
    new: int = 0  # из них новых (не было в таблице до запуска)
    removed: int = 0  # удалено из таблицы: этих строк в файле больше нет
    rejected: dict[str, int] = field(default_factory=dict)  # причина -> строк
    ambiguous_ids: list[str] = field(default_factory=list)  # id с разными данными
    merged: int = 0  # строк, ушедших в сумму (сверх первой строки пары)
    merged_pairs: list[tuple[str, str, int]] = field(default_factory=list)
    invalid_values: dict[str, int] = field(default_factory=dict)  # колонка -> не привелось к типу
    unknown: dict[str, int] = field(default_factory=dict)  # ссылки на компании не из companies
    null_counts: dict[str, int] = field(default_factory=dict)  # колонка -> NULL в таблице
    table_rows: int = 0  # строк в таблице после загрузки
    revenue_eq_1000: int | None = None
    manual_rows: int | None = None  # companies: строк source='manual' (не синхронизируются)

    @property
    def balanced(self) -> bool:
        """Каждая строка файла учтена: загружена, отклонена или сложена с другой."""
        return self.read == self.loaded + sum(self.rejected.values()) + self.merged


@dataclass
class LoadReport:
    companies: FileReport
    links: FileReport
    payments: FileReport
    persons_total: int = 0


def _clean(value: str) -> str | None:
    value = value.strip()
    return value or None


def _stage_file(conn, path: Path, spec: CsvSpec, chunk_size: int, report: FileReport) -> None:
    """Прочитать CSV порциями и скопировать в staging. Неверные строки — сразу в rejected."""
    bad: list[tuple[int, list[str]]] = []
    chunk: list[list[object]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = [h.strip() for h in next(reader, [])]
        if header != list(spec.csv_columns):
            raise ValueError(
                f"{path.name}: колонки {header} не совпадают с ожидаемыми {list(spec.csv_columns)}"
            )
        for row in reader:
            if not row:  # пустая строка в конце файла
                continue
            report.read += 1
            if len(row) != len(spec.csv_columns):
                bad.append((reader.line_num, row))
                continue
            chunk.append([reader.line_num, *(_clean(v) for v in row)])
            if len(chunk) >= chunk_size:
                repo.copy_rows(conn, spec, chunk)
                chunk = []
    if chunk:
        repo.copy_rows(conn, spec, chunk)
    if bad:
        repo.save_bad_rows(conn, path.name, bad)
        report.rejected["bad_format"] = len(bad)


def _reject(report: FileReport, reason: str, count: int) -> None:
    if count:
        report.rejected[reason] = count


def _load_file(
    conn,
    path: Path,
    spec: CsvSpec,
    model: type[Base],
    chunk_size: int,
    ambiguous_companies: list[str] | None = None,
) -> FileReport:
    report = FileReport(source_file=path.name, table=model.__tablename__)
    table = model.__tablename__

    repo.clear_rejected(conn, path.name)
    repo.create_staging(conn, spec)
    _stage_file(conn, path, spec, chunk_size, report)

    _reject(report, "missing_id", repo.reject_missing_key(conn, spec, path.name))
    if ambiguous_companies:
        # раньше полных дублей: все строки такой компании — с одной причиной
        count = repo.reject_by_company_ids(
            conn, spec, path.name, ambiguous_companies, "ambiguous_company"
        )
        _reject(report, "ambiguous_company", count)
    _reject(report, "exact_duplicate", repo.reject_exact_duplicates(conn, spec, path.name))
    if model is Company:
        report.ambiguous_ids = repo.ambiguous_keys(conn, spec)
    if model is not Payment:
        _reject(report, "ambiguous_id", repo.reject_ambiguous(conn, spec, path.name))

    report.invalid_values = repo.invalid_values(conn, spec)
    report.new = repo.count_new(conn, spec, table)
    if model is Company:
        report.loaded = repo.upsert_companies(conn, spec)
    elif model is PersonCompanyLink:
        repo.insert_persons(conn, spec)
        report.loaded = repo.upsert_links(conn, spec)
    else:
        report.merged_pairs = repo.merged_pairs(conn, spec)
        report.merged = sum(n - 1 for *_, n in report.merged_pairs)
        report.loaded = repo.upsert_payments(conn, spec)
    report.removed = repo.delete_missing(conn, spec, table)
    if model is PersonCompanyLink:
        repo.delete_persons_without_links(conn)
    repo.drop_staging(conn, spec)

    report.table_rows = repo.count_rows(conn, model)
    report.null_counts = repo.null_counts(conn, model)
    return report


def load_all(
    engine: Engine,
    companies: Path,
    links: Path,
    payments: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> LoadReport:
    """Загрузить три файла банка. Повторный запуск не создаёт дублей."""
    with engine.begin() as conn:
        comp = _load_file(conn, Path(companies), COMPANIES_SPEC, Company, chunk_size)
        comp.revenue_eq_1000 = repo.count_revenue_equal(conn, SUSPICIOUS_REVENUE)
        comp.manual_rows = repo.count_companies_by_source(conn, "manual")

        link = _load_file(conn, Path(links), LINKS_SPEC, PersonCompanyLink, chunk_size)
        link.unknown = {"synthetic_u": repo.unknown_link_companies(conn)}

        pay = _load_file(
            conn, Path(payments), PAYMENTS_SPEC, Payment, chunk_size, comp.ambiguous_ids
        )
        pay.unknown = repo.unknown_payment_sides(conn)

        persons = repo.count_rows(conn, Person)
    return LoadReport(companies=comp, links=link, payments=pay, persons_total=persons)


# --- текст отчёта ---

MAX_PAIRS_IN_REPORT = 50


def _file_section(r: FileReport) -> list[str]:
    status = "OK" if r.balanced else "НЕ СХОДИТСЯ"
    lines = [
        f"== {r.source_file} -> {r.table}",
        f"  прочитано строк:     {r.read}",
        f"  загружено:           {r.loaded} (новых {r.new}, обновлено {r.loaded - r.new})",
        f"  удалено устаревших:  {r.removed} (были в таблице, в файле их нет)",
        f"  отклонено:           {sum(r.rejected.values())}",
    ]
    lines += [f"    - {reason}: {n}" for reason, n in sorted(r.rejected.items())]
    if r.ambiguous_ids:
        shown = ", ".join(r.ambiguous_ids[:MAX_PAIRS_IN_REPORT])
        lines.append(f"  неоднозначные id (их платежи исключены): {shown}")
    if r.merged_pairs:
        lines.append(f"  сложено с другой строкой: {r.merged} (пар: {len(r.merged_pairs)})")
        for payer, benef, n in r.merged_pairs[:MAX_PAIRS_IN_REPORT]:
            lines.append(f"    - {payer} -> {benef}: строк {n}")
        if len(r.merged_pairs) > MAX_PAIRS_IN_REPORT:
            lines.append(f"    ... и ещё {len(r.merged_pairs) - MAX_PAIRS_IN_REPORT}")
    lines.append(f"  проверка: прочитано = загружено + отклонено + сложено -> {status}")
    lines.append(f"  строк в таблице:     {r.table_rows}")
    if r.manual_rows is not None:
        lines.append(
            f"  из них source='manual': {r.manual_rows} "
            "(созданы через API, синхронизацией не затронуты)"
        )
    for col, n in r.unknown.items():
        lines.append(f"  неизвестная компания ({col}): {n}")
    if r.revenue_eq_1000 is not None:
        lines.append(f"  REVENUE_COMPANY = {SUSPICIOUS_REVENUE}: {r.revenue_eq_1000}")
    if r.invalid_values:
        lines.append("  значения, не приведённые к типу (-> NULL):")
        lines += [f"    - {col}: {n}" for col, n in r.invalid_values.items()]
    nulls = {c: n for c, n in r.null_counts.items() if n}
    lines.append(
        "  NULL по колонкам: " + (", ".join(f"{c}={n}" for c, n in nulls.items()) or "нет")
    )
    return lines


def format_report(report: LoadReport) -> str:
    lines = ["Отчёт загрузки данных банка", ""]
    for r in (report.companies, report.links, report.payments):
        lines += _file_section(r) + [""]
    lines.append(f"Физлиц в таблице persons: {report.persons_total}")
    if report.payments.merged_pairs:
        lines += [
            "",
            "Вопрос к лиду: в файле платежей пара PAYER -> BENEFICIARY встречается несколько раз",
            "с разными суммами, а даты в файле нет. Сейчас суммы и количества сложены.",
            "Что означает повтор пары (другой период? другой тип платежа?) — нужно уточнить.",
        ]
    return "\n".join(lines)
