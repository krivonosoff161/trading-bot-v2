# BACKTEST_ENV_REFERENCE.md — Справка по параметрам бэктеста

> Для удалённых агентов: все бэктесты требуют локальных кэшей (.pkl) и/или OKX API.
> Запускать только локально. Qwen может предлагать конфигурации, но не запускать.

---

## Основные скрипты

| Скрипт | Назначение | Требует |
|---|---|---|
| `scripts/backtest/backtest_simulate.py` | Полный бэктест на истории | .pkl кэш + OKX public history calls for outcomes |
| `scripts/backtest/bt_sweep_drift.py` | Sweep DRIFT конфигов | .pkl кэш + запуск `backtest_simulate.py` |
| `scripts/backtest/bt_param_sweep.py` | Generic multi-config sweep | .pkl кэш |
| `scripts/backtest/bt_entry_filters.py` | Sweep entry фильтров, TP1→BE→TP2 | .pkl кэш |

---

## Env vars для бэктеста (BT_*)

### DRIFT фильтры
| Переменная | Что делает | Дефолт | Прод значение |
|---|---|---|---|
| `BT_DRIFT_VOL_DECAY_MIN` | D2: veto если vol < X × baseline | `0.9` | `0.9` |
| `BT_DRIFT_ETH_BLOCK_HOURS` | B3: ETH veto по UTC часам | `""` | `22,23,0,1` |
| `BT_DRIFT_BTC_VOL_MAX` | D3: BTC veto если vol_ratio > X | `""` | `3.0` |

### Hold times
| Переменная | Что делает | Дефолт | Прод значение |
|---|---|---|---|
| `BT_FAST_HOLD_MIN` | FAST hold (не DRIFT, не TRENDING) | `90` | `90` |
| `hold_drift_minutes` | DRIFT FAST hold (config.yaml) | — | `75` |
| `hold_trending_fast_minutes` | TRENDING FAST hold (config.yaml) | — | `120` |

### TP/SL геометрия
| Переменная | Что делает | Дефолт | Прод значение |
|---|---|---|---|
| `BT_DRIFT_TP1_K` | DRIFT TP1 как доля R | `0.5` | `0.5` |
| `BT_FAST_TP1_K` | FAST TP1 (не DRIFT) | `0.8` | `0.8` |
| `sl_k` × 1.2 | SL множитель ATR | — | `1.2` |

### Режимы запуска
| Переменная | Что делает |
|---|---|
| `OKX_IS_DEMO` | `1` = demo API, `0` = real API (для загрузки свечей) |
| `AUTO_TRADE` | `false` = только paper, `true` = реальные ордера |

---

## Локальные кэши (не в GitHub)

| Файл | Содержимое | Период |
|---|---|---|
| `scripts/backtest_candle_cache_35d.pkl` | Свечи 15m/1H/4H, 5 пар | 35 дней |
| `scripts/backtest_candle_cache_65d.pkl` | Свечи 15m/1H/4H, 5 пар | 65 дней |
| `scripts/backtest/backtest_candle_cache_65d.pkl` | То же, для bt_* скриптов | 65 дней |

Даже если candle/mark кэши есть локально, `backtest_simulate.py` всё равно делает OKX REST-запросы для forward outcome candles (`client.get_history_candles(..., "5m", after=fwd_end, limit=288)`).
Если кэша нет или он невалиден — скрипт дополнительно скачивает candle/mark/index данные через OKX API.

---

## Формулы метрик

```python
WR(TP+SL) = TP / (TP + SL)                 # TIME_EXIT исключён, используется в части отчетов
honest_WR = TP / all_signals               # TIME_EXIT включён в знаменатель
PF = sum(positive pnl/exit_r) / abs(sum(negative pnl/exit_r))
sim = симуляция баланса по фактическому PnL/exit_r выбранного скрипта
DD  = max drawdown от пика баланса
```

**Важно:** в проекте встречаются оба определения WR. Для research-отчетов всегда явно указывать, какая формула использована: `WR(TP+SL)` или `honest_WR`. TIME_EXIT нельзя скрывать: минимум показывать `te_pct` и отдельную диагностику MFE/exit_r.

---

## Параметры из config.yaml (текущий прод)

```yaml
strategy:
  ema_fast: 20
  ema_slow: 50
  adx_period: 9
  slope_min: 35
  hold_fast_minutes: 90
  hold_drift_minutes: 75
  hold_trending_fast_minutes: 120
  trending_require_fvg: true

pump_engine:
  vol_mult: 1.5
  price_pct: 1.2
  sl_atr_mult: 1.5
  tp_atr_mult: 2.5
  paper_position_usd: 100.0
  paper_balance_usd: 1000.0
  max_open_positions: 3
  fee_rt_pct: 0.10
```

---

## Как просить локальный запуск бэктеста (шаблон для Qwen)

```
Задача для локального агента (Claude Code / GPT):
- скрипт: scripts/backtest/bt_entry_filters.py
- env: BT_DRIFT_VOL_DECAY_MIN=0.9 BT_DRIFT_ETH_BLOCK_HOURS=22,23,0,1
- кэш: scripts/backtest/backtest_candle_cache_65d.pkl (локально есть)
- нужный результат: таблица WR/PF/sim по конфигам, формат markdown
- НЕ нужно: сырой JSON, отдельные сделки, логи
```

---

## Что безопасно предлагать Qwen

- Новые env var комбинации для sweep
- Изменения в логике фильтров (код)
- Review формул метрик
- Гипотезы на основе агрегированных результатов

## Что Qwen не должен делать

- Запускать бэктест самостоятельно
- Запрашивать .pkl кэши, .env, signal_log.jsonl напрямую
- Предлагать AUTO_TRADE=true без явного решения пользователя
