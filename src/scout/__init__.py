"""scout — информационная система (новое ядро проекта).

Слой инфо-эджа: сбор инфополя (скауты) → оркестрация → синтез сводки → разметка под ЛМ.
Построен НА ПОЛКЕ утилит Main (импорт, НЕ дублирование):
  src.utils.telegram · src.utils.logger · src.utils.llm_formatter · src.config · src.exchange.okx_client · src.strategy.indicators

НЕ часть торгового движка (он заморожен отдельно: src/strategy/*, src/data/*_impulse*).
Read-only research + доставка сводок. Хранилище данных: logs/scout/.
См. ARCHITECTURE.md, REFACTOR_PLAN.md.
"""
