# Бриф для GPT: аудит работы бота за 16-18.05.2026

> **Задача:** независимая проверка статистики, поведения бота и метрик. Дать развёрнутый отчёт о просадках, корректности отработки фильтров и режимов.

## Контекст

Trading Bot V2 — OKX фьючерсный скальп-бот. Три канала в paper trading:
- **Main Screener** (ws_main_screener.py) — главный канал, 15m триггер
- **BB Fade** (ws_bb_fade.py) — mean reversion, MTF 15m+5m
- **Pump engine** (ws_pump_orchestrator.py) — alt-скальп на vol_spike, 1m триггер

Все параметры в `config.yaml`. Логи: `logs/`, архив: `logs_archive/`, тейп: `E:\trading-data\ticks\<sym>\<date>.csv`.

---

## ⚠️ Найденные проблемы для верификации

### 1. Telegram silent drop вернулся
68 NOTIFY за день, все с `msg_id=None`. Anti-spam группы (ID в .env) опять активен. Маркетинговый поток не доходит.

**Проверить:** возможно проблема в формате сообщений (HTML тегах, emoji), либо action item — сменить группу/использовать канал вместо группы.

### 2. CB HALT три раза за день (18.05)
```
02:53:02 CB HALT | daily_pnl=-5.10%
13:36:55 CB HALT | daily_pnl=-4.71%
15:04:38 CB HALT | daily_pnl=-9.07% (HALTED до сих пор)
```
`cb_daily_loss_pct=4.0` срабатывает. После 120 мин cooldown reset. Защита работает, но рынок очень жесткий.

### 3. Path B обход 2nd-candle confirmation
```
CONFIRM → OPEN ratio: 110%
```
Часть OPEN идёт через `_evaluate_entry` Path B (standalone candle-close detection), минуя PENDING+CONFIRM. Это значит что **2nd-candle confirmation filter не работает на этих сделках**.

**Проверить:** [ws_pump_orchestrator.py:670-704](scripts/ws/ws_pump_orchestrator.py#L670-L704) — Path B логика. Нужно ли отключить Path B или добавить filter и для неё?

### 4. Главный паттерн потерь: MFE→SL без trailing
5 SL за день имели **MFE 2.5-4.57% в правильную сторону** до разворота:
- RIVER: MFE +4.57% → SL -2.86%
- APR: MFE +3.75% → SL -3.75%
- APR (2nd): MFE +3.36% → SL -4.36%
- BSB: MFE +2.60% → SL -1.97%
- AI: MFE +2.50% → SL -1.66%

**Сумма "съеденного" прибыли:** ~17%. Если бы был breakeven trailing (+0.5% → SL в безубыток) — этот день был бы 0% вместо -15%.

В BACKLOG это уже есть: "Chain Re-entry + Breakeven Trail" (Phase C feature).

---

## Метрики по дням (Main Screener)

### 16.05 — все TP в TRENDING, но 5 TIME
```
4 TP / 0 SL / 5 TIME → WR=100%, Sum R=+2.64
По стилям:
  SWING  1 TP / 0 SL / 0 TIME → WR=100%
  FAST   3 TP / 0 SL / 5 TIME → WR=100%
По режимам:
  TRENDING   3 TP / 0 SL / 4 TIME (5 sell signals at 07:30 — массовый сигнал в одно время)
  DRIFT      1 TP / 0 SL / 1 TIME
```
Сигналы 07:30 одновременно: GALA, HMSTR, LINEA, PUMP — массовое SELL TRENDING. Все вышли по TIME.

### 17.05 — найден баг лейблера, перелейблено
```
4 TP / 4 SL / 3 TIME → WR=50%, Sum R=-1.91
По стилям:
  FAST   2 TP / 3 SL / 3 TIME → WR=40%
  SWING  2 TP / 1 SL / 0 TIME → WR=67%
По режимам:
  TRENDING   3 TP / 3 SL / 0 TIME → WR=50%
  DRIFT      1 TP / 0 SL / 3 TIME → WR=100%
  RANGING    0 TP / 1 SL / 0 TIME → WR=0%
```

### 18.05 — отличный день (живой)
```
3 TP / 0 SL / 1 TIME / 1 pending → WR=100%, Sum R=+1.63
По стилям:
  FAST   1 TP / 0 SL / 1 TIME → WR=100%
  SWING  2 TP / 0 SL / 0 TIME → WR=100%
По режимам:
  TRENDING   3 TP / 0 SL / 1 TIME → WR=100% (SOL, NOT, TURBO — все sell)
```

---

## Метрики по дням (Pump)

| Дата | Сделок | WR | NET | PF | CB Halts |
|------|--------|----|----|-----|----------|
| 16.05 | 47 | 30% | -6.53% | 0.77 | — |
| 17.05 | 45 | 38% | +0.72% | 1.03 | — |
| 18.05 | 29 | 28% | **-14.17%** | 0.54 | **3 раза** |

### Топ-3 / Худшие-3 пары за каждый день

**16.05:**
- Топ: BILL +3.39%, GPS +2.54%, SOON +2.46%
- Худшие: AI -2.18%, EDEN -2.27%, RIVER -3.70%

**17.05:**
- Топ: RAVE +2.97%, AI +2.77%, EDEN +2.43%
- Худшие: BEAT -1.67%, BSB -2.27%, BILL -4.33%

**18.05:**
- Топ: AI +5.53%, EDEN +1.92%, PROS +1.53%
- Худшие: BABY -2.76%, BSB -3.92%, APR **-7.80%**

**Наблюдение:** AI стабильно в плюсе все 3 дня. BSB / BILL — стабильно в минусе.

---

## Эффективность фильтров (за сутки 18.05)

```
PENDING (скринер сигналы):  259
SKIP confirm (отклонено):    134 (52%)
CONFIRM (прошли):             31 (12%)
OPEN (открыто):               34 (включая Path B обход)
CLOSE (закрытия):             34
EVICT (dead vol):            224 (норма)
```

**Конверсия PENDING→OPEN: 13%** — фильтр строгий, режет 87% сигналов. Из 34 OPEN — 8 TP / 21 SL → 28% WR.

---

## Hold time распределение

```
TP: avg 17 min, min 1, max 129
SL: avg 17 min, min 1, max 433
```

433 минуты на SL — серьёзная аномалия. Скорее всего APR (94 мин hold), но 433 — это **больше 7 часов**. Найти этот сигнал в логах.

---

## Tape паттерны (примеры из pump_day_analysis.py)

### TP паттерн (EDEN +1.92% 09:15→09:17):
```
09:14  ^+0.06%  vol= 445070  buy%= 70%
09:15  ^+0.60%  vol=1805708  buy%= 58%  ← ENTRY
09:16  ^+1.61%  vol=1336682  buy%= 71%
09:17  v-0.57%  vol=1714501  buy%= 58%  ← TP
```
**Сильный объём + decisive buy% в направлении.**

### SL паттерн (DOGE -0.46% 06:20→06:25):
```
06:20  v-0.24%  vol=204436  buy%= 42%  ← ENTRY (mixed ratio)
06:21  ^+0.02%  vol= 13074  buy%= 44%
06:25  ^+0.04%  vol= 28538  buy%= 80%  ← SL (reversal — buyers came)
```
**Слабый объём после входа, разворот ratio.**

### SL паттерн (UB LONG -1.45% 09:45→09:46):
```
09:44  ^+0.81%  vol= 18248  buy%= 58%
09:45  ^+0.26%  vol= 10028  buy%= 50%  ← ENTRY
09:46  v-1.33%  vol=  7250  buy%= 28%  ← SL (instant reversal)
```
**Мгновенный разворот** — типичный stop-hunt, объём низкий.

---

## Что проверить от GPT

1. **Подтвердить или опровергнуть** мою гипотезу про breakeven trail
   - Симулировать на этих 5 SL что было бы если бы breakeven сработал
   - Какие пороги breakeven дают лучший trade-off?
2. **Path B обход confirmation** — критично или нет?
   - Сравнить WR Path A (через CONFIRM) vs Path B
   - Если Path B WR значительно хуже → отключить
3. **Стабильно убыточные пары:** BILL / BSB / RIVER / APR
   - Стоит ли вводить permanent blacklist или работают временно `session_ban_sl_no_tp`?
4. **TRENDING массовые сигналы 07:30** (16.05)
   - Все 5 пар одновременно — это система или один внешний фактор?
   - Нужен ли cooldown между сигналами на разных парах в одном режиме?
5. **Hold time max=433 min** на SL — найти этот случай, понять не повис ли мониторинг
6. **Tape паттерны** — buy% ratio как фильтр входа:
   - Совпадает ли паттерн "слабый объём + mixed ratio = SL" во всех 21 SL?
   - Какой порог по vol/buy% даст max WR на бэктесте 3 дней?

---

## Файлы для копания

### Данные:
- `logs/signals/main_signals.jsonl` — main signals
- `logs/signals/main_signals_labels.jsonl` — main outcomes (после фикса 17.05)
- `logs/bb_fade/bb_fade_signals.jsonl` — BB Fade
- `logs/pump/pump_signals.jsonl` — pump entries
- `logs/pump/pump_labels.jsonl` — pump exits (с MFE/MAE)
- `logs/pump/ws_pump_orchestrator.log` — runtime лог pump
- `E:\trading-data\ticks\<sym>\<date>.csv` — тейп

### Скрипты для анализа:
- `scripts/analysis/pump_day_analysis.py <YYYY-MM-DD>` — tape срезы
- `scripts/build_journal.py` — обновить Excel журнал
- `scripts/analysis/label_main_ws.py` — лейблер main (исправлен 17.05)

### Конфиг:
- `config.yaml` секции: `pump_orchestrator`, `main_screener`, `bb_fade`

### Архив (исторические сделки):
- `logs_archive/09.05.2026/pump/` — pump engine v1/v2 logs (старая логика которая давала +87% за 5 дней)
- Сравнение vs текущий orchestrator (-41% за 8 дней)

---

## Цель отчёта от GPT

1. **Подтвердить/опровергнуть** мои основные находки (breakeven, Path B, проблемные пары)
2. **Найти то, что я пропустил** — особенно по поведению фильтров
3. **Численно оценить** что было бы при разных параметрах (breakeven 0.3%, 0.5%, 0.8% / SL move to entry at 1R)
4. **Топ-3 практических улучшения** с ожидаемым эффектом в %

После отчёта GPT — обсуждаем и внедряем.
