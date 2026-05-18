# Бриф для GPT #2: бэктест pair_risk_overrides

> **Задача:** перед внедрением pair_risk_overrides в production код — прогнать симуляцию на исторических данных. Решить какой вариант (если вообще) внедряем.

## Что уже внедрено в коде (после твоего отчёта)

Коммит `9d4efa5`. Три изменения активны:

1. **`load_dotenv()` в оркестраторе** — Telegram доставка работает (msg_id живые)
2. **`enable_path_b: false`** — Path B обход 2nd-candle confirmation отключен
3. **Breakeven trail** — `breakeven_trail_enabled: true`, `breakeven_trigger_r: 1.0`
   - Когда MFE достигает 1R → SL переезжает на entry
   - SL hit после armed → exit_reason="BE" (не считается SL для streak/cooldown)

**Эти 3 фикса уже в production. Любая твоя симуляция должна считать их активными.**

## Что хочется проверить

В отчёте предложил pair_risk_overrides:

```yaml
pair_risk_overrides:
  BABY-USDT-SWAP: { size_mult: 0.0 }
  RIVER-USDT-SWAP: { size_mult: 0.0 }
  APR-USDT-SWAP: { size_mult: 0.5, require_breakeven: true }
  BSB-USDT-SWAP: { size_mult: 0.5, require_breakeven: true }
  BILL-USDT-SWAP: { max_trades_per_day: 2, ban_after_sl_streak: 2 }
```

**Гипотеза автора:** часть SL по проблемным парам (BSB, APR, RIVER) **превратится в BE** после внедрения breakeven trail. Если так — pair_risk_overrides может стать избыточным или вредным (зарежет потенциальные TP).

## Задача симуляции

### Базовая линия (Sim0): "After 3 fixes"
- Период: 16-18.05.2026 (текущие данные `logs/pump/pump_labels.jsonl`)
- Применить breakeven simulation: для SL-сделок где `mfe_r >= 1.0` → exit_reason="BE", net = -0.10% (fee)
- Path B уже отключен — удалить 3 Path B сделок 18.05
- Это baseline для сравнения

### Сценарии для теста

| Sim | Что меняется vs Sim0 | Что измерить |
|-----|----------------------|--------------|
| **Sim1** | BABY: size_mult=0.0 (полный бан) | net delta, кол-во удалённых сделок |
| **Sim2** | BABY + RIVER: size_mult=0.0 | net delta |
| **Sim3** | + APR: size_mult=0.5 (половина PnL) | net delta |
| **Sim4** | + BSB: size_mult=0.5 | net delta |
| **Sim5** | + BILL: max_trades_per_day=2, ban_after_sl_streak=2 | net delta, какие сделки отрезаются |
| **Sim6** | **ВСЕ overrides** (full GPT recipe) | финальный net |
| **Sim7** | Только `ban_after_sl_streak=2` (динамически для всех пар) | net delta, выживет ли BILL |

### Ключевые метрики на каждой симуляции

```
Total trades after cuts: N
Net %: X.XX%
Delta vs Sim0: ±YY.YY п.п.
TP / BE / SL counts
Sharpe-like ratio (если посчитаешь): avg_net / std_net
```

### Сравнение для финального вердикта

- Какие пары после **breakeven** перестают быть проблемными?
- Какой override даёт max impact с min cuts?
- Где overfit (т.е. убираем сделки которые завтра могут быть TP)?

## Дополнительный контекст для симуляции

### Sample size warning

3 дня = 121 pump trade. Это **маленькая выборка** для finalize. Если есть возможность:
- Прогони на расширенном окне: **с 03.05.2026** включая `logs_archive/09.05.2026/pump/pump_labels.jsonl`
- Старый pump engine = другая архитектура, но **те же пары** — статистически валидно для решения "какие пары хронически плохие"

### Что НЕ симулировать (за рамками задачи)

- Не меняй параметры breakeven (1R уже выбран)
- Не меняй параметры входа (vol_mult, price_pct, etc.)
- Не добавляй новые фичи (tape filter и т.д.) — это уже Phase G

## Что хочу видеть в отчёте

`docs/gpt_pair_overrides_backtest.md`:

1. **Sim0 baseline numbers** (after-3-fixes на 16-18.05 + расширенно)
2. **Таблица Sim0-Sim7** с net и deltas
3. **Топ-2 рекомендации** — что внедрять / что не стоит
4. **Конкретный YAML** для production config

После твоего отчёта — обсудим что внедрять.

## Открытые риски, на которые обратить внимание

- **Sample bias:** BSB и APR — у них **MFE→SL pattern**. После breakeven они уже не должны быть проблемой. Pair override может стать **повторной защитой ИЛИ over-protection**.
- **BILL** имел **сильные TP** 16.05/17.05 (+1.34%, +2.49%, +2.74%, +1.61%). `max_trades_per_day=2` может зарезать эти TP.
- **BABY** — 5/0 за 3 дня, но 5 сделок недостаточно для permanent decision. **`ban_after_sl_streak=2`** мягче и адаптивно.

## Финальный вопрос автора

**После симуляции:** какой минимум overrides даёт реальный value, и есть ли вообще смысл их вводить **если breakeven уже сработал**?
