"""SQL загрузки CSV банка.

Схема: строки CSV копируются (COPY) во временную staging-таблицу, где все колонки —
текст. Дальше SQL-запросами по staging отбраковываем строки (они уходят в
rejected_rows), приводим типы и переносим в основные таблицы через
INSERT ... ON CONFLICT DO UPDATE — повторный запуск обновляет, а не дублирует.
Строки, которых в файле больше нет, из таблицы удаляются: таблица = последний файл.

Имена таблиц и колонок в SQL подставляются только из CsvSpec (константы в коде),
значения из файла — только через COPY и параметры.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import Company, Payment, PersonCompanyLink, RejectedRow
from app.models.base import Base

# целое или десятичное число: 136493.0, -5, 1000
NUMBER_RE = r"^-?[0-9]+(\.[0-9]+)?$"
TRUE_VALUES = ("1", "1.0")
FALSE_VALUES = ("0", "0.0")


@dataclass(frozen=True)
class CsvSpec:
    """Описание CSV: колонки в порядке файла и как их приводить к типам.

    Колонки в БД называются как в CSV, но в нижнем регистре.
    """

    staging: str
    csv_columns: tuple[str, ...]
    key: tuple[str, ...]
    bool_columns: tuple[str, ...] = ()
    # числа: в колонке bigint, исходный текст «не числа» — в колонке <имя>_raw
    number_columns: tuple[str, ...] = ()

    @property
    def columns(self) -> list[str]:
        return [c.lower() for c in self.csv_columns]


# --- выражения приведения типов ---


def _bool(col: str) -> str:
    true_values = ", ".join(f"'{v}'" for v in TRUE_VALUES)
    false_values = ", ".join(f"'{v}'" for v in FALSE_VALUES)
    return (
        f"CASE WHEN {col} IN ({true_values}) THEN true "
        f"WHEN {col} IN ({false_values}) THEN false END"
    )


def _number(col: str) -> str:
    return f"CASE WHEN {col} ~ '{NUMBER_RE}' THEN round({col}::numeric)::bigint END"


def _raw(col: str) -> str:
    # пустое значение (NULL) в _raw не попадает: NULL !~ ... даёт NULL
    return f"CASE WHEN {col} !~ '{NUMBER_RE}' THEN {col} END"


def _typed_columns(spec: CsvSpec) -> tuple[list[str], list[str]]:
    """Колонки целевой таблицы и SQL-выражения для них из staging."""
    names: list[str] = []
    exprs: list[str] = []
    for col in spec.columns:
        if col in spec.bool_columns:
            names.append(col)
            exprs.append(_bool(col))
        elif col in spec.number_columns:
            names += [col, f"{col}_raw"]
            exprs += [_number(col), _raw(col)]
        else:
            names.append(col)
            exprs.append(col)
    return names, exprs


# --- staging ---


def create_staging(conn: Connection, spec: CsvSpec) -> None:
    cols = ", ".join(f"{c} text" for c in spec.columns)
    conn.execute(text(f"DROP TABLE IF EXISTS {spec.staging}"))
    conn.execute(
        text(
            f"CREATE TEMP TABLE {spec.staging} (row_no integer PRIMARY KEY, {cols}) ON COMMIT DROP"
        )
    )


def drop_staging(conn: Connection, spec: CsvSpec) -> None:
    conn.execute(text(f"DROP TABLE IF EXISTS {spec.staging}"))


def copy_rows(conn: Connection, spec: CsvSpec, rows: Sequence[Sequence[object]]) -> None:
    """Порция строк в staging через COPY (быстрее, чем INSERT). None -> NULL."""
    cols = ", ".join(["row_no", *spec.columns])
    raw = conn.connection.driver_connection  # соединение psycopg внутри текущей транзакции
    with raw.cursor() as cur, cur.copy(f"COPY {spec.staging} ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


# --- отбраковка строк ---


def clear_rejected(conn: Connection, source_file: str) -> None:
    """Убрать отклонённые строки прошлой загрузки этого файла (их заменит текущая)."""
    conn.execute(delete(RejectedRow).where(RejectedRow.source_file == source_file))


def _move_to_rejected(
    conn: Connection,
    spec: CsvSpec,
    source_file: str,
    reason: str,
    where: str,
    params: dict[str, object] | None = None,
) -> int:
    """Удалить строки из staging и записать их в rejected_rows. Вернуть число строк."""
    pairs = zip(spec.csv_columns, spec.columns, strict=True)
    raw_json = ", ".join(f"'{csv}', {col}" for csv, col in pairs)
    sql = f"""
        WITH moved AS (
            DELETE FROM {spec.staging} WHERE {where} RETURNING *
        ), saved AS (
            INSERT INTO rejected_rows (source_file, row_number, raw_data, reason)
            SELECT :source_file, row_no, jsonb_build_object({raw_json}), :reason FROM moved
            ON CONFLICT (source_file, row_number, reason) DO NOTHING
        )
        SELECT count(*) FROM moved
    """
    params = {**(params or {}), "source_file": source_file, "reason": reason}
    return conn.execute(text(sql), params).scalar_one()


def reject_missing_key(conn: Connection, spec: CsvSpec, source_file: str) -> int:
    where = " OR ".join(f"{k} IS NULL" for k in spec.key)
    return _move_to_rejected(conn, spec, source_file, "missing_id", where)


def reject_exact_duplicates(conn: Connection, spec: CsvSpec, source_file: str) -> int:
    """Полностью одинаковые строки: первая остаётся, остальные — exact_duplicate."""
    cols = ", ".join(spec.columns)
    where = f"""row_no IN (
        SELECT row_no FROM (
            SELECT row_no, row_number() OVER (PARTITION BY {cols} ORDER BY row_no) AS n
            FROM {spec.staging}
        ) d WHERE n > 1
    )"""
    return _move_to_rejected(conn, spec, source_file, "exact_duplicate", where)


def reject_ambiguous(conn: Connection, spec: CsvSpec, source_file: str) -> int:
    """Один ключ — разные данные: отклоняем все такие строки (ambiguous_id)."""
    keys = ", ".join(spec.key)
    where = f"""({keys}) IN (
        SELECT {keys} FROM {spec.staging} GROUP BY {keys} HAVING count(*) > 1
    )"""
    return _move_to_rejected(conn, spec, source_file, "ambiguous_id", where)


def ambiguous_keys(conn: Connection, spec: CsvSpec) -> list[str]:
    """Ключи, которые встречаются в staging больше одного раза (для ключа из одной колонки)."""
    (key,) = spec.key
    sql = f"SELECT {key} FROM {spec.staging} GROUP BY {key} HAVING count(*) > 1 ORDER BY {key}"
    return list(conn.execute(text(sql)).scalars())


def reject_by_company_ids(
    conn: Connection, spec: CsvSpec, source_file: str, ids: list[str], reason: str
) -> int:
    """Строки, где любая колонка ключа (id компании) входит в ids, — в rejected_rows."""
    if not ids:
        return 0
    where = " OR ".join(f"{k} = ANY(:ids)" for k in spec.key)
    return _move_to_rejected(conn, spec, source_file, reason, where, {"ids": ids})


def save_bad_rows(conn: Connection, source_file: str, rows: list[tuple[int, list[str]]]) -> None:
    """Строки с неверным числом полей: в staging они не попадают, пишем сразу."""
    stmt = insert(RejectedRow).values(
        [
            {
                "source_file": source_file,
                "row_number": line,
                "raw_data": {"values": values},
                "reason": "bad_format",
            }
            for line, values in rows
        ]
    )
    conn.execute(stmt.on_conflict_do_nothing(constraint="uq_rejected_rows_source"))


def invalid_values(conn: Connection, spec: CsvSpec) -> dict[str, int]:
    """Сколько значений не удалось привести к типу (флаг не 0/1, число не число)."""
    allowed = ", ".join(f"'{v}'" for v in TRUE_VALUES + FALSE_VALUES)
    parts = [
        f"count(*) FILTER (WHERE {c} IS NOT NULL AND {c} NOT IN ({allowed})) AS {c}"
        for c in spec.bool_columns
    ] + [f"count(*) FILTER (WHERE {c} !~ '{NUMBER_RE}') AS {c}" for c in spec.number_columns]
    if not parts:
        return {}
    row = conn.execute(text(f"SELECT {', '.join(parts)} FROM {spec.staging}")).mappings().one()
    return {k: v for k, v in row.items() if v}


def merged_pairs(conn: Connection, spec: CsvSpec) -> list[tuple[str, str, int]]:
    """Ключи, у которых в staging больше одной строки (их будем складывать)."""
    keys = ", ".join(spec.key)
    sql = f"""
        SELECT {keys}, count(*) FROM {spec.staging}
        GROUP BY {keys} HAVING count(*) > 1 ORDER BY {keys}
    """
    return [tuple(r) for r in conn.execute(text(sql))]


# --- перенос в основные таблицы ---


def _upsert(conn: Connection, spec: CsvSpec, table: str, extra: dict[str, str]) -> int:
    names, exprs = _typed_columns(spec)
    names += list(extra)
    exprs += list(extra.values())
    updates = ", ".join(f"{n} = EXCLUDED.{n}" for n in names if n not in spec.key)
    sql = f"""
        INSERT INTO {table} ({", ".join(names)})
        SELECT {", ".join(exprs)} FROM {spec.staging} s
        ON CONFLICT ({", ".join(spec.key)}) DO UPDATE SET {updates}
    """
    return conn.execute(text(sql)).rowcount


def upsert_companies(conn: Connection, spec: CsvSpec) -> int:
    return _upsert(conn, spec, "companies", {})


def upsert_links(conn: Connection, spec: CsvSpec) -> int:
    known = "EXISTS (SELECT 1 FROM companies c WHERE c.synthetic_u = s.synthetic_u)"
    return _upsert(conn, spec, "person_company_links", {"is_known_company": known})


def insert_persons(conn: Connection, spec: CsvSpec) -> None:
    conn.execute(
        text(
            f"INSERT INTO persons (synthetic_f) SELECT DISTINCT synthetic_f FROM {spec.staging} "
            "ON CONFLICT DO NOTHING"
        )
    )


def upsert_payments(conn: Connection, spec: CsvSpec) -> int:
    """Платежи: повторы пары складываются (после удаления полных дублей).

    Если в паре есть нечисловое значение, сумма = NULL, исходные тексты — в *_raw.
    """
    sums = []
    for col in ("pay_amt", "pay_cnt"):
        sums.append(
            f"CASE WHEN bool_or(s.{col} !~ '{NUMBER_RE}') THEN NULL "
            f"ELSE sum({_number('s.' + col)})::bigint END"
        )
        sums.append(f"string_agg({_raw('s.' + col)}, '; ' ORDER BY s.row_no)")
    sql = f"""
        INSERT INTO payments (payer, beneficiary, pay_amt, pay_amt_raw, pay_cnt, pay_cnt_raw,
                              payer_is_known, beneficiary_is_known, merged_rows)
        SELECT s.payer, s.beneficiary, {", ".join(sums)},
               EXISTS (SELECT 1 FROM companies c WHERE c.synthetic_u = s.payer),
               EXISTS (SELECT 1 FROM companies c WHERE c.synthetic_u = s.beneficiary),
               count(*)
        FROM {spec.staging} s
        GROUP BY s.payer, s.beneficiary
        ON CONFLICT (payer, beneficiary) DO UPDATE SET
            pay_amt = EXCLUDED.pay_amt, pay_amt_raw = EXCLUDED.pay_amt_raw,
            pay_cnt = EXCLUDED.pay_cnt, pay_cnt_raw = EXCLUDED.pay_cnt_raw,
            payer_is_known = EXCLUDED.payer_is_known,
            beneficiary_is_known = EXCLUDED.beneficiary_is_known,
            merged_rows = EXCLUDED.merged_rows
    """
    return conn.execute(text(sql)).rowcount


def _key_match(spec: CsvSpec, left: str, right: str) -> str:
    return " AND ".join(f"{left}.{k} = {right}.{k}" for k in spec.key)


def count_new(conn: Connection, spec: CsvSpec, table: str) -> int:
    """Сколько ключей из staging ещё нет в таблице (будут вставлены, а не обновлены)."""
    keys = ", ".join(spec.key)
    sql = f"""
        SELECT count(*) FROM (SELECT DISTINCT {keys} FROM {spec.staging}) s
        WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {_key_match(spec, "t", "s")})
    """
    return conn.execute(text(sql)).scalar_one()


def delete_missing(conn: Connection, spec: CsvSpec, table: str) -> int:
    """Удалить из таблицы строки, ключей которых нет в текущей загрузке."""
    sql = f"""
        DELETE FROM {table} t
        WHERE NOT EXISTS (SELECT 1 FROM {spec.staging} s WHERE {_key_match(spec, "t", "s")})
    """
    return conn.execute(text(sql)).rowcount


def delete_persons_without_links(conn: Connection) -> int:
    sql = """
        DELETE FROM persons p
        WHERE NOT EXISTS (SELECT 1 FROM person_company_links l WHERE l.synthetic_f = p.synthetic_f)
    """
    return conn.execute(text(sql)).rowcount


# --- цифры для отчёта ---


def count_rows(conn: Connection, model: type[Base]) -> int:
    return conn.execute(select(func.count()).select_from(model)).scalar_one()


def null_counts(conn: Connection, model: type[Base]) -> dict[str, int]:
    table = model.__table__
    stmt = select(*[func.count().filter(c.is_(None)).label(c.name) for c in table.columns])
    return dict(conn.execute(stmt).mappings().one())


def count_revenue_equal(conn: Connection, value: int) -> int:
    stmt = select(func.count()).where(Company.revenue_company == value)
    return conn.execute(stmt).scalar_one()


def unknown_link_companies(conn: Connection) -> int:
    stmt = select(func.count()).where(PersonCompanyLink.is_known_company.is_(False))
    return conn.execute(stmt).scalar_one()


def unknown_payment_sides(conn: Connection) -> dict[str, int]:
    payer = Payment.payer_is_known.is_(False)
    benef = Payment.beneficiary_is_known.is_(False)
    stmt = select(
        func.count().filter(payer).label("payer"),
        func.count().filter(benef).label("beneficiary"),
        func.count().filter(payer | benef).label("any"),
    )
    return dict(conn.execute(stmt).mappings().one())
