# DRIFT Quality Test Map — GPT Implementation Brief

**Цель:** улучшить DRIFT WR с 74% до ≥80%, сохранив ≥60 сигналов из 96 baseline.  
**Принцип:** один тест = одна переменная, всё остальное заморожено.

---

## 1. Архитектура — что нужно знать

### Файлы
| Файл | Роль |
|---|---|
| `scripts/backtest/backtest_simulate.py` | Полный бэктест — ЭТО главный файл для изменений |
| `src/strategy/signal_engine.py` | Продовый движок (НЕ трогать в этом таске) |
| `src/strategy/indicators.py` | `find_swing_levels`, `calc_atr`, `calc_ema` и т.д. |
| `scripts/backtest/backtest_candle_cache_65d.pkl` | Кэш свечей 63 дня, 6 пар |

### Ключевые факты о коде backtest_simulate.py
- **`raw_15m` — newest-first** (raw_15m[0] = текущая формирующаяся, raw_15m[1] = последняя закрытая)
- **Vol ratio**: `vols_15m[1:4]` = последние 3 закрытых бара, `vols_15m[5:20]` = baseline
- **`close = float(c15[-1])`** — последняя цена (oldest-first в parsed array)
- **`atr_15m`** — ATR(9) на 15m, уже вычислен
- **`vwap`** — внутридневной VWAP от открытия UTC-дня, может быть None рано утром
- **`signal_hour = dt_last.hour`** — UTC час сигнала (0–23)
- **`swings = find_swing_levels(h15, l15, lookback=3, count=4)`** — вычисляется для SL/TP в блоке FAST, dict с `recent_highs` и `recent_lows` (lists, oldest→newest)
- **DRIFT block** начинается на `elif regime in ("DRIFT", "WEAK_TREND"):` → `drift_base` → entry assignment
- **Фильтры ПОСЛЕ drift_base**: `drift_short_veto` → `slope veto` → `vwap_ok` → `drift_adx1h_veto`
- **Env vars** для свипов уже используются: `BT_HOLD_FAST_M`, `BT_SLOPE_MIN`, `BT_DRIFT_TP1_K`, `BT_DRIFT_MAX_VOL`
- **`_drop`** — строка причины блокировки, пишется первым совпавшим фильтром

### Baseline (63d, 5 пар: BTC/ETH/SOL/XRP/DOGE)
| Метрика | OVERALL | DRIFT only |
|---|---|---|
| Сигналов | ~362 | ~96 |
| WR | 87% | 74% |
| PF | ~3.28 | ~1.8 |
| Симуляция | +105% | (входит в OVERALL) |

### Пары и DRIFT vol thresholds (из `_PAIR_PARAMS`)
| Пара | DRIFT `vol` threshold |
|---|---|
| BTC-USDT | 1.2 |
| ETH-USDT | 1.8 |
| SOL-USDT | 2.0 |
| XRP-USDT | 1.3 |
| DOGE-USDT | 1.3 |

---

## 2. Тесты — 4 фильтра + 1 комбинированный

### Общее правило добавления фильтра
Каждый фильтр вставляется **в конце DRIFT блока** — после присвоения `trade_style, side, entry_cfg = "FAST", "buy"/"sell", cfg_d`, но **до** `strong_4h_veto` и `drift_short_veto`. Используем паттерн:
```python
if trade_style == "FAST" and side and <condition>:
    trade_style, side = "NO_TRADE", None
    _drop = _drop or "<filter_name>"
```

---

### TEST-A: DRIFT VWAP Stretch Filter

**Гипотеза:** DRIFT убыточен когда цена уже сильно ушла от VWAP — это остаточный дрейф после основного движения, а не начало нового.

**Реализация:**
```python
# TEST-A: DRIFT VWAP stretch veto
_DRIFT_VWAP_STRETCH = float(os.getenv("BT_DRIFT_VWAP_STRETCH", "999.0"))
if trade_style == "FAST" and side and vwap and atr_15m > 0:
    vwap_stretch = abs(close - vwap) / atr_15m
    if vwap_stretch > _DRIFT_VWAP_STRETCH:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "drift_vwap_stretch"
```

**Свип параметров:**
| Конфиг | BT_DRIFT_VWAP_STRETCH | Ожидаемый эффект |
|---|---|---|
| A0 (baseline) | 999.0 (выкл) | — |
| A1 | 1.5 | агрессивный |
| A2 | 1.25 | умеренный (старт) |
| A3 | 1.0 | строгий |

**Добавить в вывод для каждого конфига:**
- DRIFT vwap_stretch distribution (p25, p50, p75, p90)
- Blocked сигналов по парам
- WR/PF оставшихся DRIFT сигналов

---

### TEST-B: ETH DRIFT Time Filter

**Гипотеза:** ETH DRIFT ломается в UTC 13-16 (после NYSE open) и UTC 22-01 (после US close) из-за повышенной новостной чувствительности и jump risk.

**Реализация:**
```python
# TEST-B: ETH DRIFT hour veto
_DRIFT_ETH_BLOCK_HOURS_RAW = os.getenv("BT_DRIFT_ETH_BLOCK_HOURS", "")
_DRIFT_ETH_BLOCK_HOURS = [int(h) for h in _DRIFT_ETH_BLOCK_HOURS_RAW.split(",") if h.strip().isdigit()]
if trade_style == "FAST" and side and symbol == "ETH-USDT" and signal_hour in _DRIFT_ETH_BLOCK_HOURS:
    trade_style, side = "NO_TRADE", None
    _drop = _drop or "drift_eth_hour"
```

**Свип параметров:**
| Конфиг | BT_DRIFT_ETH_BLOCK_HOURS | Смысл |
|---|---|---|
| B0 | "" (выкл) | baseline |
| B1 | "13,14,15" | только NYSE open |
| B2 | "13,14,15,16,17,18,19,20" | NYSE open + продолжение |
| B3 | "22,23,0,1" | после US close |
| B4 | "13,14,15,22,23,0,1" | оба окна |

**Добавить в вывод:**
- ETH DRIFT breakdown по часам UTC (hour × WR × n)
- DRIFT ETH общий WR до/после

---

### TEST-C: Move-From-Base Filter

**Гипотеза:** если цена уже прошла N × ATR_15m от последнего свинг-минимума (для лонга) или максимума (для шорта), импульс уже выдохся — мы входим поздно.

**Реализация:**
```python
# TEST-C: DRIFT late entry — move from last swing base
_DRIFT_MOVE_FROM_BASE = float(os.getenv("BT_DRIFT_MOVE_FROM_BASE", "999.0"))
if trade_style == "FAST" and side and atr_15m > 0:
    _swings_c = find_swing_levels(h15, l15, lookback=3, count=4)
    if side == "buy" and _swings_c["recent_lows"]:
        _base = _swings_c["recent_lows"][-1]   # nearest swing low (oldest→newest, last = most recent)
        _move = (close - _base) / atr_15m
        if _move > _DRIFT_MOVE_FROM_BASE:
            trade_style, side = "NO_TRADE", None
            _drop = _drop or "drift_late_from_base"
    elif side == "sell" and _swings_c["recent_highs"]:
        _base = _swings_c["recent_highs"][-1]  # nearest swing high
        _move = (_base - close) / atr_15m
        if _move > _DRIFT_MOVE_FROM_BASE:
            trade_style, side = "NO_TRADE", None
            _drop = _drop or "drift_late_from_base"
```

**ВАЖНО:** `find_swing_levels` уже вызывается ниже для SL/TP. Чтобы не дублировать — либо вызвать один раз раньше (до DRIFT block) и переиспользовать, либо принять дублирование для изоляции теста.

**Свип параметров:**
| Конфиг | BT_DRIFT_MOVE_FROM_BASE | Ожидаемый эффект |
|---|---|---|
| C0 | 999.0 (выкл) | baseline |
| C1 | 1.8 | мягкий |
| C2 | 1.5 | умеренный (старт) |
| C3 | 1.2 | строгий |

**Добавить в вывод:**
- Распределение `move_from_base` у WR-winners vs losers (p25, p50, p75)
- Blocked сигналов по парам

---

### TEST-D: Volume Decay Filter

**Гипотеза:** если trigger бар имеет объём ниже среднего (импульс уже потух) — это поздний вход на затухании.

**Реализация:**
```python
# TEST-D: DRIFT volume decay veto
# trigger bar = raw_15m[1] (newest closed bar, newest-first)
_DRIFT_VOL_DECAY_MIN = float(os.getenv("BT_DRIFT_VOL_DECAY_MIN", "0.0"))
if trade_style == "FAST" and side and _DRIFT_VOL_DECAY_MIN > 0:
    _prior_mean = float(np.mean(vols_15m[5:20])) if len(vols_15m) >= 20 else 0.0
    _trigger_vol_ratio = vols_15m[1] / max(_prior_mean, 1e-9) if _prior_mean > 0 else 1.0
    if _trigger_vol_ratio < _DRIFT_VOL_DECAY_MIN:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "drift_vol_decay"
```

**Примечание:** `vol_ratio` (уже вычисленный) = среднее 3 баров к baseline. TEST-D проверяет именно trigger бар (index 1), а не среднее. Это строже.

**Свип параметров:**
| Конфиг | BT_DRIFT_VOL_DECAY_MIN | Ожидаемый эффект |
|---|---|---|
| D0 | 0.0 (выкл) | baseline |
| D1 | 0.7 | мягкий |
| D2 | 0.9 | умеренный |
| D3 | 1.0 | строгий (trigger должен быть выше avg) |

---

### TEST-E: Combined Late Entry Score

**Гипотеза:** ни один фильтр по отдельности не даст нужного эффекта. Composite score из A+C+D лучше отсекает плохие DRIFT сигналы.

**Реализация:**
```python
# TEST-E: Late Entry Score (composite A+C+D)
_LATE_SCORE_MAX = int(os.getenv("BT_LATE_SCORE_MAX", "99"))
if trade_style == "FAST" and side and _LATE_SCORE_MAX < 99:
    _late_score = 0
    # Factor 1: VWAP stretch > 1.25 ATR
    if vwap and atr_15m > 0 and abs(close - vwap) / atr_15m > 1.25:
        _late_score += 1
    # Factor 2: move from last swing base > 1.5 ATR
    _swings_e = find_swing_levels(h15, l15, lookback=3, count=4)
    if side == "buy" and _swings_e["recent_lows"]:
        if (close - _swings_e["recent_lows"][-1]) / max(atr_15m, 1e-9) > 1.5:
            _late_score += 1
    elif side == "sell" and _swings_e["recent_highs"]:
        if (_swings_e["recent_highs"][-1] - close) / max(atr_15m, 1e-9) > 1.5:
            _late_score += 1
    # Factor 3: trigger bar vol < 0.9× baseline
    _prior_e = float(np.mean(vols_15m[5:20])) if len(vols_15m) >= 20 else 0.0
    if _prior_e > 0 and vols_15m[1] / _prior_e < 0.9:
        _late_score += 1
    if _late_score > _LATE_SCORE_MAX:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or f"drift_late_score_{_late_score}"
```

**Свип параметров:**
| Конфиг | BT_LATE_SCORE_MAX | Смысл |
|---|---|---|
| E0 | 99 (выкл) | baseline |
| E1 | 2 | блокировать при score >= 3 |
| E2 | 1 | блокировать при score >= 2 |

---

## 3. Порядок запуска

```
1. Baseline прогон (все BT_ переменные = default/disabled) → сохранить как BASELINE
2. TEST-A: 4 конфига (A0 уже = baseline, A1/A2/A3)
3. TEST-B: 5 конфигов (B0=baseline, B1/B2/B3/B4)
4. TEST-C: 4 конфига (C0=baseline, C1/C2/C3)
5. TEST-D: 4 конфига (D0=baseline, D1/D2/D3)
6. TEST-E: 3 конфига (E0=baseline, E1/E2)
   — лучший конфиг из A+C+D как фиксированные параметры для E
7. BEST COMBINED: лучший A + лучший C (или D) + проверка E
```

Итого: **~24 прогона**. Каждый прогон сохранять как `backtest_runs/<TAG>_<КОНФИГ>.txt` через `BT_RUN_TAG`.

---

## 4. Требуемый формат отчёта

### 4.1 Заголовок каждого прогона
```
═══════════════════════════════════════════════════════
  TEST: <TEST_ID> | Config: <env vars> | Tag: <BT_RUN_TAG>
═══════════════════════════════════════════════════════
```

### 4.2 OVERALL (как сейчас — не менять)
Период P1/P2/P3 × WR/PF/sim/DD — сохранить текущий формат.

### 4.3 DRIFT-SPECIFIC раздел (НОВЫЙ — добавить)
```
─── DRIFT BREAKDOWN ───────────────────────────────────────────────────────
  Total DRIFT signals:   XX  (baseline: 96)
  Blocked by new filter: XX  (XX%)
  Remaining DRIFT:       XX
  
  DRIFT WR:  XX%  (baseline: 74%)
  DRIFT PF:  X.XX (baseline: ~1.8)
  DRIFT avg_R: X.XX

  По парам:
  BTC  n=XX  WR=XX%  PF=X.XX
  ETH  n=XX  WR=XX%  PF=X.XX
  SOL  n=XX  WR=XX%  PF=X.XX
  XRP  n=XX  WR=XX%  PF=X.XX
  DOGE n=XX  WR=XX%  PF=X.XX

  По периодам:
  P1 (0-21d):  DRIFT n=XX WR=XX%
  P2 (21-42d): DRIFT n=XX WR=XX%
  P3 (42-63d): DRIFT n=XX WR=XX%
```

### 4.4 Hour Analysis (только для TEST-B и когда BT_HOUR_ANALYSIS=1)
```
─── DRIFT HOUR ANALYSIS (UTC) ─────────────────────────────────────────────
  hour | n  | WR%  | blocked
  00   | XX | XX%  | XX
  01   | XX | XX%  | XX
  ...
  23   | XX | XX%  | XX
```

### 4.5 Distribution stats (только когда BT_DIST_ANALYSIS=1)
Для TEST-A и TEST-C — вывести распределение метрики по winners/losers:
```
─── DRIFT METRIC DISTRIBUTION ─────────────────────────────────────────────
  Metric: vwap_stretch [abs(close-vwap)/atr_15m]
           winners (TP)    losers (SL/TIME)
  p25:     X.XX            X.XX
  p50:     X.XX            X.XX
  p75:     X.XX            X.XX
  p90:     X.XX            X.XX
  mean:    X.XX            X.XX
```

### 4.6 Filter Funnel (НОВЫЙ — добавить в конец каждого прогона)
```
─── SIGNAL FUNNEL ─────────────────────────────────────────────────────────
  All evaluated:           XXXX
  → REGIME not DRIFT:      XXXX  (XX%)
  → DRIFT: drift_vol_low:  XX    (XX% of DRIFT)
  → DRIFT: drift_vwap_stretch: XX (TEST-A)
  → DRIFT: drift_eth_hour: XX    (TEST-B)
  → DRIFT: drift_late_from_base: XX (TEST-C)
  → DRIFT: drift_vol_decay: XX   (TEST-D)
  → DRIFT: slope_weak:     XX
  → DRIFT: drift_adx1h_low: XX
  → DRIFT: drift_short_veto: XX
  → DRIFT passed all filters: XX
  ─────────────────────────────
  TRENDING passed: XX
  RANGING passed:  XX
  TOTAL ENTRIES:   XX
```

### 4.7 Сводная таблица всех прогонов (в конце последнего прогона или отдельный скрипт)
```
─── SWEEP SUMMARY ─────────────────────────────────────────────────────────
Tag              | DRIFT_n | DRIFT_WR | DRIFT_PF | OVERALL_WR | OVERALL_PF | Sim
BASELINE         |  96     |  74%     |  1.8     |  87%       |  3.28      | +105%
A1_stretch_1.5   |  XX     |  XX%     |  X.XX    |  XX%       |  X.XX      | +XX%
A2_stretch_1.25  |  XX     |  XX%     |  X.XX    |  XX%       |  X.XX      | +XX%
...
```

---

## 5. Критерии Accept/Reject

### Accept если:
- DRIFT WR ≥ 80% **И** DRIFT PF ≥ 2.0
- DRIFT n ≥ 60 (не потеряли больше 37% сигналов)
- OVERALL WR не упал ниже 85%
- P3 DRIFT WR ≥ 70% (не переоптимизация под P1/P2)

### Reject если:
- WR растёт, но PF падает (режем лучшие сигналы по R-величине)
- P1/P2 WR растёт, но P3 WR падает ниже 65% (overfit)
- DRIFT n < 55 (слишком мало данных для статистики)
- OVERALL sim падает ниже +80%

### Приоритет при конфликте:
`DRIFT PF > DRIFT WR > signal_count` — один хороший DRIFT лучше 5 плохих.

---

## 6. Технические требования к имплементации

1. **Все новые фильтры — только env vars**, не хардкодить в PARAM_SETS
2. **Совместимость с baseline**: при default env vars поведение идентично текущему
3. **`_drop` назначать только если `_drop is None`** (паттерн `_drop = _drop or "..."`)
4. **Funnel считать через `defaultdict(int)` `drop_counter`** — инкрементировать для каждого отброшенного тика
5. **DRIFT-specific stats собирать отдельно** от OVERALL stats в `drift_results = []`
6. **Machine-readable JSON** (опционально): после текстового вывода сохранять `backtest_runs/<TAG>_summary.json` с ключами `{tag, overall_wr, overall_pf, drift_n, drift_wr, drift_pf, sim_pct}`

---

## 7. Что НЕ трогать

- `src/strategy/signal_engine.py` — только бэктест
- TRENDING, RANGING, RANGING_RECOVERY блоки — не менять логику
- SL/TP формулы — не менять  
- `_DRIFT_ADX1H_MIN = 15.0` и `drift_short_veto` — уже в проде, оставить
- Файл кэша `backtest_candle_cache_65d.pkl` — не регенерировать без явного запроса

---

## 8. Уже протестировано — НЕ повторять

| Гипотеза | Результат |
|---|---|
| ATR_1H для SL вместо ATR_15m | WR +2%, баланс -46pts → хуже |
| Hold FAST 480m/SWING 720m | WR -9%, DD×2 → хуже |
| DRIFT ADX min 12→8 | DRIFT WR 66%→62%, больше шума |
| BB compression filter (vol>4 AND bb_width<0.6) | никогда не срабатывает |
| Trigger extension veto (5m far from EMA20) | WR падает — большое extension = сильный сигнал |
| Independent ATR TP/SL | WR 81%→57%, SL streak 3→9 → хуже |
