# Qwen Brief — Pump Engine B.5.4 Fix

**Проект:** Trading Bot V2 — OKX фьючерсный скальпинг
**GitHub (private):** `krivonosoff161/trading-bot-v2`
**Задача:** диагностика и фиксы pump engine. WR=30.5%, цель >55%.

---

## 1. КОНТЕКСТ ПРОЕКТА

Два параллельных трека:
- **Трек 1 — Concierge Analyzer:** основной бизнес, TG-бот с сигналами для 4 подписчиков
- **Трек 2 — Pump Engine:** WS скальпинг alt-coin пампов, paper trading

Pump Engine сейчас в **Фазе B.5** — минимальные фиксы чтобы поднять WR до >55% перед переходом к Phase C (ws_smart_pump.py — 7-слойная архитектура).

---

## 2. АРХИТЕКТУРА PUMP ENGINE (текущая)

```
ws_screener_live.py           ws_pump_orchestrator.py
   232 пары                         4 main slots
   vol+move фильтр      →       pool: main/counter/banned
   active_universe.json          SL: 1.5×ATR, TP: 2.5×ATR
                                 cooldown: 120s per pair
```

**Файлы:**
- `scripts/ws/ws_pump_orchestrator.py` — основной движок
- `scripts/ws/ws_screener_live.py` — отдельный живой скринер (не трогаем)
- `config.yaml` секция `pump_orchestrator`
- `logs/pump/pump_signals.jsonl` — ENTRY сигналы
- `logs/pump/pump_labels.jsonl` — EXIT с P&L

**Формат pump_signals.jsonl:**
```json
{"signal_id": "79e43176", "type": "ENTRY", "ts_utc": "2026-05-11T03:00:00Z",
 "sym": "LAYER-USDT-SWAP", "section": "main", "trade_count": 1,
 "direction": "PUMP", "signal_close": 0.12565,
 "paper_tp": 0.12744, "paper_sl": 0.124576,
 "vol_ratio": 2.626, "pct_move": 1.429, "dollar_vol": 323313.0, "atr": 0.000716}
```

**Формат pump_labels.jsonl:**
```json
{"signal_id": "79e43176", "type": "EXIT", "sym": "LAYER-USDT-SWAP",
 "exit_reason": "TP", "entry_price": 0.12565, "exit_price": 0.12744,
 "gross_pnl_pct": 1.4246, "fee_pct": 0.1, "net_pnl_pct": 1.3246,
 "hold_min": 1.0, "section": "main", "opened_at": "...", "closed_at": "..."}
```

---

## 3. ТЕКУЩИЕ ПАРАМЕТРЫ (config.yaml → pump_orchestrator секция)

```yaml
pump_orchestrator:
  max_main_slots: 4
  universe_poll_sec: 1
  vol_mult: 1.5          # порог vol spike
  price_pct: 1.2         # порог price move %
  min_usd_vol: 30000
  pump_phase_max_pct: 5.0
  alert_cooldown_sec: 120  # кулдаун между входами на одной паре
  position_usd: 100.0
  fee_rt_pct: 0.10
  sl_atr_mult: 1.5
  tp_atr_mult: 2.5
  paper_max_hold_min: 9999
  main_sl_to_counter: 2    # SL подряд → counter секция
  counter_sl_to_ban: 2     # SL в counter → бан 24ч
  cb_cooldown_sl: 3        # circuit breaker SL streak
  cb_cooldown_min: 30
  cb_daily_loss_pct: 5.0
  heartbeat_interval: 30
```

---

## 4. РЕЗУЛЬТАТЫ (факты, 10.05–11.05.2026)

### Общая статистика (61 сделка нового оркестратора):
| Метрика | Значение |
|---|---|
| WR (TP vs SL) | **30.5%** (18 TP / 41 SL) |
| NET PnL | **-28.84%** |
| Avg hold при TP | **33.1 мин** |
| Avg hold при SL | **11.4 мин** ← ключевой сигнал |

### По парам:
| Пара | n | TP | SL | WR% | Net% | Avg hold |
|---|---|---|---|---|---|---|
| LAYER-USDT-SWAP | 19 | 7 | 12 | 37% | -2.8% | 11m |
| BILL-USDT-SWAP | 7 | 2 | 5 | 29% | -5.0% | 23m |
| OPG-USDT-SWAP | 4 | 0 | 3 | 0% | -2.7% | 7m |
| BEAT-USDT-SWAP | 4 | 2 | 2 | 50% | +0.8% | 11m |
| PROS-USDT-SWAP | 3 | 0 | 3 | 0% | -3.0% | 3m |
| WAL-USDT-SWAP | 3 | 0 | 2 | 0% | -5.2% | 7m |
| RLS-USDT-SWAP | 2 | 0 | 2 | 0% | -1.5% | 3m |
| TRUTH-USDT-SWAP | 2 | 0 | 2 | 0% | -3.4% | 15m |

### Распределение по vol_ratio:
| Диапазон | Сигналов | % от всех |
|---|---|---|
| 1.5–2.0x | 19 | 31% |
| 2.0–3.0x | 18 | 30% |
| 3.0–5.0x | 10 | 16% |
| 5.0x+ | 14 | 23% |

### Распределение по price move:
| Диапазон | Сигналов | % от всех |
|---|---|---|
| 1.2–1.5% | 18 | 30% |
| 1.5–2.0% | 23 | 38% |
| 2.0–3.0% | 15 | 25% |
| 3.0%+ | 5 | 8% |

---

## 5. ДИАГНОСТИКА (что мы видим)

### Проблема 1: Chasing the pump (главная)
SL бьётся за **11 минут** vs TP за **33 минуты**.
Это означает: мы входим ПОСЛЕ пика. Цена дала импульс 1.2–2%, мы входим — она сразу разворачивается. При этом если памп реальный — он держится 30+ минут.

**Гипотеза:** фильтр vol_mult=1.5 и price_pct=1.2% слишком слабый. Мы ловим:
- Конец настоящего пампа (вошли поздно)
- MM vol wash (объём есть, движения нет)
- Случайный шум 1.5x vol

### Проблема 2: LAYER доминирует (19/61 = 31%)
С кулдауном 120с пара может сигналить каждые 2 мин. WAL дал 3 сигнала за 5 минут.
Это один и тот же памп — мы входим несколько раз в одно движение.

### Проблема 3: Pool thrashing
Пары добавляются в pool → через 5-10 мин скринер их убирает → оркестратор тоже убирает.
Pool падает до main=3 вместо 4. Нет времени накопить историю по паре.

### Проблема 4: PROS/OPG/RLS — 0% WR
Эти пары дали 0 TP. Возможно, это системно плохие пары для данной стратегии (MM manipulation или специфика токена). Нужен per-pair ban после N SL.

---

## 6. ЗАДАЧИ ДЛЯ QWEN

### Задача A — Анализ качества по vol/price порогам

Нужно понять: какой vol_mult и price_pct дают лучший WR?

Данные в `logs/pump/pump_signals.jsonl` (поля: vol_ratio, pct_move) и `logs/pump/pump_labels.jsonl` (поле: exit_reason).

Связь: `signal_id` в signals → `signal_id` в labels.

**Нужен sweep:** для каждой комбинации (vol_mult ∈ {1.5, 2.0, 2.5, 3.0}, price_pct ∈ {1.2, 1.5, 2.0}) посчитать:
- Сколько сигналов проходит фильтр
- WR на прошедших
- NET PnL

Это поможет определить оптимальные пороги.

### Задача B — Анализ оптимального кулдауна

Факт: LAYER дал 19 сигналов. С кулдауном 10 мин — дал бы ≈3.

Нужно проанализировать: если убрать сигналы на той же паре в течение X минут после предыдущего — как меняется WR?

Данные: `pump_labels.jsonl` → opened_at + sym + exit_reason.

Симуляция: для кулдауна ∈ {2, 5, 10, 15, 30} мин — фильтровать re-entries и смотреть WR на оставшихся.

### Задача C — Предложение фиксов кода

На основе диагностики предложить конкретные изменения в `ws_pump_orchestrator.py`:

1. **Min dwell time:** пара остаётся в pool минимум N минут (даже если screener убрал из universe). Где добавить и как именно.

2. **Per-pair SL ban (жёстче чем counter):** если PROS/OPG/RLS дали 0 TP за N сделок → сессионный бан без перехода в counter.

3. **Entry timing:** вместо входа на spike-свече — ждать откат (следующую свечу). Это решает chasing.

Формат ответа: конкретные строки кода или diff, не просто описание.

---

## 7. КОД ОРКЕСТРАТОРА (полный)

`scripts/ws/ws_pump_orchestrator.py` — ключевые методы:

### _refresh_pool (логика ротации пула):
```python
async def _refresh_pool(self) -> None:
    screener_meta = self._read_universe()
    async with self.pool_lock:
        now = time.time()
        added, removed = [], []

        # 1. Update last_signal_at for existing pool pairs still in screener
        for sym in self.pool:
            if sym in screener_meta:
                self.pool[sym].last_signal_at = now

        # 2. Remove pairs that left screener (frees slots before adding new ones)
        for sym in list(self.pool.keys()):
            state = self.pool[sym]
            if sym not in screener_meta and state.position is None and state.section != "banned":
                await self._remove_pair(sym)
                removed.append(sym)

        # 3. Fill empty slots with new screener pairs
        for sym, meta in screener_meta.items():
            if sym in self.pool:
                continue
            main_count = sum(1 for p in self.pool.values() if p.section == "main")
            if main_count >= int(self.config["max_main_slots"]):
                break
            # ... добавляем пару
```

### _evaluate_entry (логика входа):
```python
async def _evaluate_entry(self, state: PairState, candle: Candle) -> None:
    sym = state.sym
    now = time.time()

    # Cooldown between entries
    if now - self.last_signal_wall.get(sym, 0.0) < float(self.config["alert_cooldown_sec"]):
        return

    history = self.feed.get_candles(sym, "candle1m", 11)
    if len(history) < 11:
        return

    current = history[-1]
    previous = history[-11:-1]

    baseline_vol = sum(row[5] for row in previous) / 10.0
    vol_spike = current[5] / baseline_vol
    price_move = abs(current[4] - current[1]) / current[1] * 100.0

    if vol_spike < float(self.config["vol_mult"]) or price_move < float(self.config["price_pct"]):
        return

    # Stagnation filter
    if vol_spike >= 2.0 and price_move < 0.5:
        return

    dollar_vol = current[6]
    if dollar_vol < float(self.config["min_usd_vol"]):
        return

    atr = sum(abs(row[2] - row[3]) for row in previous) / 10.0

    # Determine direction
    if state.section == "main":
        side = "buy" if current[4] > current[1] else "sell"
        state.main_direction = side
    else:
        side = "sell" if state.main_direction == "buy" else "buy"

    # Pump phase gate
    if state.section == "main" and state.baseline_price > 0:
        phase_pct = abs(current[4] - state.baseline_price) / state.baseline_price * 100.0
        if phase_pct > float(self.config["pump_phase_max_pct"]):
            return

    self.last_signal_wall[sym] = now
    state.trade_count += 1
    await self._open_position(state, current, side, vol_spike, price_move, dollar_vol, atr)
```

### PairState dataclass:
```python
@dataclass
class PairState:
    sym: str
    section: str           # "main" | "counter" | "banned"
    main_direction: str
    baseline_price: float
    added_at: float
    last_signal_at: float
    main_sl_streak: int = 0
    counter_sl_streak: int = 0
    total_sl_streak: int = 0
    sl_today: int = 0
    tp_today: int = 0
    trade_count: int = 0
    cooldown_until: float = 0.0
    banned_until: float = 0.0
    position: OpenPosition | None = None
```

---

## 8. ОГРАНИЧЕНИЯ (что НЕ делаем)

- **Не переписываем в ws_smart_pump.py** — это Phase C, только после WR>55%
- **Не добавляем OI/funding** — Phase C
- **Не трогаем ws_screener_live.py** — отдельная система
- **Не меняем SL/TP структуру** — только пороги входа и кулдаун
- **Минимальные изменения** — максимум 3-4 правки

---

## 9. ОЖИДАЕМЫЙ РЕЗУЛЬТАТ

1. **Конкретные цифры** для config.yaml: оптимальные vol_mult, price_pct, alert_cooldown_sec
2. **Конкретный код** для min dwell time в `_refresh_pool` (15-20 строк max)
3. **Опционально:** идея entry timing (входить на следующей свече после spike)

Целевые показатели: WR >55%, NET PnL >0% на следующих 50 сделках.
