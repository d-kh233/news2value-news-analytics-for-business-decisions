"""Выгружает JSON Schema всех контрактов в schemas_json/ (для бэка, фронта и README).
Запуск: python -m llm.export_schemas"""
import json
from pathlib import Path

from . import schemas as s

OUT = Path(__file__).with_name("schemas_json")
MODELS = [s.EventExtraction, s.RelevanceAssessment, s.AgentPlan, s.Insight, s.CriticVerdict, s.ChatAnswer, s.Digest]

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for m in MODELS:
        (OUT / f"{m.__name__}.json").write_text(
            json.dumps(m.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8")
        print("ok", m.__name__)
