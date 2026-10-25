"""Контракт API не сломан с B1: изменения только в виде добавлений.

База — схема модуля B1 (коммит 584f766, tests/fixtures/openapi_b1.json). Схемы сравниваются по
содержанию, а не по именам ($ref раскрываются): Page_Insight_ стал InsightPage — это не поломка.
Проверяем: все пути и методы B1 на месте; коды ответов B1 на месте; поля ответов не пропали и тип
у них тот же; значения enum не удалены; в запросах нет новых обязательных полей и параметров.
Отдельно: docs/openapi.json совпадает с текущей схемой (его не забыли перегенерировать).
"""

import json
from pathlib import Path
from typing import Any

from app.main import app

BASELINE = Path(__file__).parent / "fixtures" / "openapi_b1.json"
DOCS = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
METHODS = {"get", "post", "put", "delete", "patch"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Spec:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc

    def resolve(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        seen = 0
        while schema and "$ref" in schema and seen < 20:
            name = schema["$ref"].rsplit("/", 1)[-1]
            schema = self.doc["components"]["schemas"][name]
            seen += 1
        return schema or {}

    def variants(self, schema: dict[str, Any]) -> list[dict[str, Any]]:
        """anyOf/oneOf (например, «строка или null») -> список раскрытых вариантов."""
        schema = self.resolve(schema)
        parts = schema.get("anyOf") or schema.get("oneOf")
        if parts:
            return [v for p in parts for v in self.variants(p)]
        return [schema]

    def types(self, schema: dict[str, Any]) -> set[str]:
        out = set()
        for v in self.variants(schema):
            t = v.get("type") or ("object" if "properties" in v else "any")
            out.add(t)
        return out

    def enum(self, schema: dict[str, Any]) -> set[Any]:
        return {e for v in self.variants(schema) for e in v.get("enum", [])} | {
            v["const"] for v in self.variants(schema) if "const" in v
        }

    def props(self, schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for v in self.variants(schema):
            out.update(v.get("properties", {}))
        return out

    def required(self, schema: dict[str, Any]) -> set[str]:
        return {r for v in self.variants(schema) for r in v.get("required", [])}

    def items(self, schema: dict[str, Any]) -> dict[str, Any] | None:
        for v in self.variants(schema):
            if v.get("type") == "array":
                return v.get("items")
        return None


def _compare_output(old: Spec, new: Spec, a: dict, b: dict, where: str, errors: list[str]) -> None:
    """Ответ: всё, что было, осталось с тем же типом; enum не сузился. Новое — можно."""
    ta, tb = old.types(a), new.types(b)
    if "any" not in ta and not ta <= tb | {"any"}:
        errors.append(f"{where}: тип {sorted(ta)} -> {sorted(tb)}")
        return
    ea, eb = old.enum(a), new.enum(b)
    if ea and eb and not ea <= eb:
        errors.append(f"{where}: из enum удалены {sorted(ea - eb, key=str)}")
    pa, pb = old.props(a), new.props(b)
    for name, sub in pa.items():
        if name not in pb:
            errors.append(f"{where}.{name}: поле пропало")
            continue
        _compare_output(old, new, sub, pb[name], f"{where}.{name}", errors)
    for name in old.required(a) - new.required(b):
        if name in pb:
            errors.append(f"{where}.{name}: поле было обязательным в ответе, стало необязательным")
    ia, ib = old.items(a), new.items(b)
    if ia is not None and ib is not None:
        _compare_output(old, new, ia, ib, f"{where}[]", errors)


def _compare_input(old: Spec, new: Spec, a: dict, b: dict, where: str, errors: list[str]) -> None:
    """Тело запроса: новых обязательных полей нет; поля B1 принимаются с тем же типом."""
    new_required = new.required(b) - old.required(a)
    if new_required:
        errors.append(f"{where}: новые обязательные поля {sorted(new_required)}")
    pa, pb = old.props(a), new.props(b)
    for name, sub in pa.items():
        if name not in pb:
            errors.append(f"{where}.{name}: поле запроса больше не принимается")
        elif not old.types(sub) <= new.types(pb[name]) | {"any"}:
            errors.append(f"{where}.{name}: тип {old.types(sub)} -> {new.types(pb[name])}")
        else:
            ea, eb = old.enum(sub), new.enum(pb[name])
            if ea and eb and not ea <= eb:
                errors.append(f"{where}.{name}: из enum удалены {sorted(ea - eb, key=str)}")


def _schema_of(body: dict[str, Any] | None) -> dict[str, Any]:
    return ((body or {}).get("content") or {}).get("application/json", {}).get("schema", {})


def contract_errors(base: dict[str, Any], current: dict[str, Any]) -> list[str]:
    old, new = Spec(base), Spec(current)
    errors: list[str] = []
    for path, ops in base["paths"].items():
        for method, op in ops.items():
            if method not in METHODS:
                continue
            where = f"{method.upper()} {path}"
            cur = current["paths"].get(path, {}).get(method)
            if cur is None:
                errors.append(f"{where}: эндпоинт пропал")
                continue
            # параметры: старые на месте, новых обязательных нет
            params_old = {(p["name"], p["in"]): p for p in op.get("parameters", [])}
            params_new = {(p["name"], p["in"]): p for p in cur.get("parameters", [])}
            for key, p in params_old.items():
                q = params_new.get(key)
                if q is None:
                    errors.append(f"{where}: параметр {key[0]} пропал")
                elif not old.types(p.get("schema", {})) <= new.types(q.get("schema", {})) | {"any"}:
                    errors.append(f"{where}: тип параметра {key[0]} изменился")
                elif not p.get("required") and q.get("required"):
                    errors.append(f"{where}: параметр {key[0]} стал обязательным")
            for key, q in params_new.items():
                if key not in params_old and q.get("required"):
                    errors.append(f"{where}: новый обязательный параметр {key[0]}")
            if "requestBody" in op:
                _compare_input(
                    old,
                    new,
                    _schema_of(op["requestBody"]),
                    _schema_of(cur.get("requestBody")),
                    f"{where} (тело)",
                    errors,
                )
            elif cur.get("requestBody", {}).get("required"):
                errors.append(f"{where}: появилось обязательное тело запроса")
            for code, resp in op.get("responses", {}).items():
                if code == "422":
                    continue  # стандартная ошибка FastAPI, у нас она в форме ErrorResponse
                cur_resp = cur.get("responses", {}).get(code)
                if cur_resp is None:
                    errors.append(f"{where}: пропал ответ {code}")
                    continue
                _compare_output(
                    old, new, _schema_of(resp), _schema_of(cur_resp), f"{where} {code}", errors
                )
    return errors


def test_current_api_only_adds_to_b1():
    errors = contract_errors(_load(BASELINE), app.openapi())
    assert errors == [], "\n".join(errors)


def test_checker_catches_breaking_changes():
    """Сам тест ловит поломки: пропавшее поле, смену типа, новое обязательное поле запроса."""
    base = _load(BASELINE)
    broken = json.loads(json.dumps(base))
    schemas = broken["components"]["schemas"]
    del schemas["Insight"]["properties"]["summary"]
    schemas["Insight"]["properties"]["importance"] = {"type": "string"}
    schemas["FeedbackRequest"]["required"] = ["rating", "comment"]
    schemas["Insight"]["properties"]["type"]["enum"] = ["risk"]
    errors = "\n".join(contract_errors(base, broken))
    assert "summary: поле пропало" in errors
    assert "importance: тип" in errors
    assert "новые обязательные поля ['comment']" in errors
    assert "из enum удалены" in errors


def test_docs_openapi_is_up_to_date():
    """docs/openapi.json перегенерирован: uv run python scripts/export_openapi.py."""
    assert _load(DOCS) == json.loads(json.dumps(app.openapi(), ensure_ascii=False))
