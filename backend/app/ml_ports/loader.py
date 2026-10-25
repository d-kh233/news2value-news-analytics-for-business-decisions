"""Выбор ML-функций по ML_MODE (B8+B9): одно место вместо проверок режима по всему коду.

  ML_MODE=real — адаптеры real.py (агент и llm/ с таймаутом, повтором и MLError);
  ML_MODE=fake — заглушки: fake_port и ветки fake в digest_port / qa_port.
Кэш llm_cache и журнал audit_log стоят на функциях портов и работают в обоих режимах.
Режим читается при каждом вызове get_ports(): в тестах и скриптах его можно переключать.
"""

from app.core.config import get_settings
from app.ml_ports import digest_port, fake_port, qa_port
from app.ml_ports.base import MLPorts


def get_ports() -> MLPorts:
    if get_settings().ml_mode == "real":
        from app.ml_ports import real

        return MLPorts(
            mode="real",
            generate_insights=real.generate_insights,
            digest_summary=real.digest_summary,
            answer_question=real.answer_question,
        )
    return MLPorts(
        mode="fake",
        generate_insights=fake_port.generate_insights,
        digest_summary=digest_port.digest_summary,
        answer_question=qa_port.answer_question,
    )
