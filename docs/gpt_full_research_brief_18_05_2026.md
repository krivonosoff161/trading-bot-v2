# Бриф для GPT #3: Полное исследование системы — единый отчёт

> **Это финальный research-бриф.** Один отчёт, без диалога. Цель — определить **рабочую конфигурацию системы** на основании ВСЕХ доступных данных. После этого отчёта **план и бэклог проекта перепишутся**.

## Роль

Ты — **senior quantitative researcher** на алготрейдинговом проекте. У тебя есть:
- Полный доступ к коду, логам, архивам, тейпу
- Право предлагать любые изменения в торговой логике
- Запрет менять production-код напрямую — ты пишешь отчёт, изменения проводят люди после ревью
- Свобода глубины: тратить токены имеет смысл **только** на полный отчёт. Не пиши промежуточных писем.

## Контекст проекта

Bot v2 — OKX фьючерсный скальп-бот, 3 канала в paper trading:
- **Main Screener** (ws_main_screener.py) — главный канал, 15m триггер, режимы DRIFT/TRENDING/RANGING/CHOPPY, стили FAST/SWING/FADE
- **BB Fade** (ws_bb_fade.py) — отдельный mean reversion, MTF 15m+5m wick rejection
- **Pump Engine** (ws_pump_orchestrator.py) — alt-скальп на vol_spike, 1m

Все три **прибыльны в paper**, но **сильно недоиспользуют данные**:
- Текущий main читает только OHLCV свечи + индикаторы (~30-40% доступного сигнала)
- Tape (50M тиков, `E:\trading-data\ticks\`) записывается, но НЕ используется в live
- OI / Funding / Liquidations — недоступны main, но OKX WS их даёт
- Smart Money паттерны, Volume Profile, кросс-актив корреляция — нигде

**Главный insight автора:** "мы зарабатываем профит на одном режиме (FAST DRIFT WR=94%) при использовании 30-40% данных. **Что будет если включить остальные 60%?**"

## ⚠️ Жёсткие ограничения

1. **НЕ менять production-код в `scripts/ws/*.py` и `config.yaml`** — только предлагать в отчёте
2. **Все изменения в отдельный документ** `docs/gpt_research_*.md`, для последующего ревью
3. **Не ломать работающее** — бот сейчас торгует paper, ничего не должно его остановить
4. Анализ может **создавать новые скрипты в `scripts/analysis/research/`** для одноразовых прогонов
5. Использовать ВСЕ данные, включая архив. Если данных не хватает за период — указать и **попросить дозагрузить через OKX REST** (бот не на твоём железе)

## Источники данных

### Live (текущие, ~8 дней)
- `logs/signals/main_signals.jsonl` — Main WS сигналы (77)
- `logs/signals/main_signals_labels.jsonl` — лейблы (82, after bugfix 17.05)
- `logs/bb_fade/bb_fade_signals.jsonl` — BB Fade (3 сигнала + labels внутри)
- `logs/pump/pump_signals.jsonl` — pump ENTRY (513)
- `logs/pump/pump_labels.jsonl` — pump EXIT с MFE/MAE (488)
- `logs/pump/ws_pump_orchestrator.log` — runtime лог

### Архив (исторические, ~5.5 недель)
- `logs_archive/signals/signal_log_2026-05.jsonl` — старый scanner Май (168)
- `logs_archive/09.05.2026/signals/signal_log.jsonl` (170) + `signal_labels.jsonl` (170)
- `logs_archive/09.05.2026/pump/pump_signals.jsonl` (361) + `pump_labels.jsonl` (234)
- `logs_archive/fade/*` — старая BB Fade research data

### Tape (тики) — на другом диске
- Path: `E:\trading-data\ticks\<SYMBOL>-USDT-SWAP\<YYYY-MM-DD>.csv` (или `.csv.gz`)
- Период покрытия: с ~11.04.2026, рваный (не все пары/дни)
- Формат: timestamp_ms, price, size, side ("buy"/"sell")
- Объём: ~50M записей всего

### Конфиг
- `config.yaml` — секции: `pump_orchestrator`, `main_screener`, `bb_fade`, `strategy`

### Уже проведённые анализы (читать как контекст, не повторять)
- `docs/gpt_audit_report_18_05_2026.md` — твой предыдущий отчёт (breakeven, Path B, telegram fix)
- `docs/gpt_brief_pair_overrides_18_05_2026.md` — задача на pair overrides backtest
- `docs/gpt_audit_brief_18_05_2026.md` — мой предыдущий бриф

### Pre-deployed fixes (учитывай как baseline)
Коммит `9d4efa5`. Активны:
- `load_dotenv()` в orchestrator → Telegram доставка живая
- `enable_path_b: false` → Path B отключен
- `breakeven_trail_enabled: true, breakeven_trigger_r: 1.0` → SL→entry на MFE 1R

Все симуляции должны учитывать эти фиксы.

---

# 📋 Задачи (3 блока)

## Блок 1 — MAIN SCREENER

### 1.1 По режимам (DRIFT / TRENDING / RANGING / CHOPPY)

Данные доступны (post-bugfix 17.05 + архив, dedup 263 labeled):

| Режим | Decisive | WR | Sample |
|-------|----------|----|----|
| DRIFT (комбинированно FAST+FADE) | 96+ | ~75% | 🟢 solid |
| TRENDING (FAST+SWING) | 69+ | ~67% | 🟢 solid |
| RANGING (FADE+FAST) | 27 | ~62% | 🟡 prelim |
| CHOPPY | <5 | — | 🔴 N/A |

**Что сделать:**
1. **Раздельно по каждому режиму:** найти sub-buckets (по hour, by_pair, by_side, by_adx_4h_bucket, by_vol_ratio) с устойчивым WR ≥75% при n≥10.
2. **Antibuckets:** найти sub-buckets с WR ≤30% при n≥10 — потенциальные SKIP-фильтры.
3. **Сезонность по часам UTC** для каждого режима отдельно.
4. **Прогноз:** сколько SL/TIME можно убрать если внедрить найденные фильтры. Симуляция как было бы:
   - Без фильтров (baseline)
   - С topN-фильтрами (топ-3 разделителя)
   - С totalban antibuckets

### 1.2 По стилям (FAST / SWING / FADE)

| Style × Regime | Decisive | WR | AvgR | Сэмпл |
|----------------|----------|----|----|------|
| FAST × DRIFT | 78 | 81% | +0.13 | 🟢 |
| SWING × TRENDING | 54 | 69% | **-0.14** | 🟢 ⚠️ |
| FADE × RANGING | 24 | 62% | ~ | 🟡 |
| FAST × TRENDING | 15 | 73% | ~ | 🟡 |
| FADE × DRIFT | 18 | 56% | ~ | 🟡 |

**Главный фокус — SWING TRENDING.** Единственный бакет с **AvgR<0** при WR>60%. Это значит:
- Sample: 54 decisive (37 TP / 17 SL / 2 TIME)
- WR=69% — кажется ОК, но **средний TP1 R = ~0.5R, средний SL = -1R** → math не сходится

**Что сделать:**
1. **Точная причина почему SWING TRENDING убыточен.** Гипотезы:
   - TP1 геометрия (R_to_TP1 слишком мал)
   - Sub-bucket плохой (например ETH SWING TRENDING — может конкретно ETH тащит)
   - Hour bias
   - Стиль входа (на закрытии 1H бара после long trending move = поздний вход)
2. **Симуляция:** при каком R_to_TP1 minimum threshold SWING TRENDING становится прибыльным? (например только сигналы где TP1≥0.8R)
3. **Или нужно вообще отключить?** Симуляция полного отключения SWING TRENDING.

### 1.3 По параметрам (advanced)
1. **Slope effect:** есть ли корреляция slope_15m с outcome?
2. **MFE > 0.5R but TIME:** сколько TIME exits были в плюсе на 0.5R+ (т.е. могли бы быть TP с другими параметрами)?
3. **MAE distribution:** сколько SL имели MAE < 0.5R до достижения стопа (= защита Late Entry Detection могла бы их избежать)?

---

## Блок 2 — BB FADE

Текущая статистика **очень мала**: 3 сигнала live (1 TP / 1 SL / 1 TIME). Но **архив scanner имеет 47 FADE сигналов** (60% WR, +0.6R cumul).

### 2.1 Архив FADE pattern mining

Файлы:
- `logs_archive/09.05.2026/signals/signal_log.jsonl` + labels — 170 сигналов, выбрать только `style=="FADE"`
- `logs_archive/fade/` — что там есть?

**Что сделать:**
1. **Sub-buckets** по тем же осям что для main: hour, regime, pair, side
2. **Лучшие условия для FADE:** найти setup с WR≥75% и подвыборкой ≥10
3. **Найти worst sub-buckets** — где BB Fade систематически плох

### 2.2 New algorithm validation

Новый `ws_bb_fade.py` (с 15.05) использует **другой алгоритм**: 15m setup → 5m wick rejection. Текущий R:R фильтр поднят до 0.5 (был 0.3).

**Что сделать:**
1. Прогнать на архиве **что бы дал новый алгоритм** на тех же данных где старый дал 60% WR (если возможно реконструировать MTF из архивных 5m+15m свечей через OKX REST history)
2. **Сравнить:** новый wick rejection vs старый "five_m_fade_hint" в `signal_engine.py`
3. **Гипотеза tape buy_ratio:** на 35 покрытых тейпом BB Fade сигналах в архиве — ratio 0.5-0.7 → WR=75%. Подтвердить или опровергнуть на расширенной выборке (использовать tape для всех BB Fade сигналов где есть локальный CSV).

### 2.3 Recommendation
1. Какие config параметры BB Fade оптимальны (`min_width_pct`, `max_vol_ratio`, `rsi_sell_max`, `rsi_buy_min`, `adx_trending_min`)
2. Когда BB Fade лучше OFF (например в TRENDING на 4H — не fade)
3. Можно ли объединить старый scanner FADE-сигналы (внутри scanner_loop) с новым ws_bb_fade — или они дают разные edge?

---

## Блок 3 — PUMP ENGINE

### 3.1 Historical comparison — "было лучше"

**Архив 03-09.05.2026** (pump engine v1/v2 — старый код):
- 234 сделки, WR=43%, NET=**+79.29%** (peak +87% на 08.05)
- median hold 4 мин
- avg TP +2.07%, avg SL -0.87% (payoff ratio 2.4)

**Current 09-18.05** (новый orchestrator):
- 488 сделок, WR=37%, NET=**-41%**
- median hold 7 мин
- avg TP +1.67%, avg SL -1.12% (payoff ratio 1.5)

**Что сделать:**
1. **Walk git commits 03-09.05** — найти точные параметры config.yaml на каждую дату. Конкретно:
   - `vol_mult`, `price_pct`, `min_usd_vol`, `alert_cooldown_sec`
   - `cb_*`, `session_ban_sl_no_tp`, `confirmation_reversal_max_pct`
   - Любые **отключённые сейчас фильтры** которые тогда были on
2. **Параметрический diff:** какие именно параметры изменились между прибыльным периодом (06-08.05) и убыточным (09-18.05)
3. **Замечание:** возможно дело не в параметрах, а в *рынке* — отдельно проверить crypto vol/trend на эти периоды и сравнить

### 3.2 Текущее состояние + симуляция

С учётом 3 pre-deployed fixes (commit 9d4efa5):

**Что сделать:**
1. Применить найденные "old winning params" к симуляции на текущих данных 09-18.05. Получится ли восстановить +%?
2. **Если получится** → конкретный YAML с предложенными изменениями
3. **Если НЕ получится** → значит дело в рынке. Тогда фокус на адаптивность под рынок (volatility regime, BTC trend state)

### 3.3 С использованием ВСЕХ данных

Эта часть — **самая важная**. Сейчас pump использует только candle vol/price. У нас есть:

| Источник | Где | Покрытие |
|----------|-----|----------|
| Tape per pair | `E:\trading-data\ticks\<sym>\<date>.csv` | Рваное, ~50% сделок |
| OKX OI WS | НЕ записываем live | Можно дозагрузить через history API? |
| OKX Funding WS | НЕ записываем live | Раз в 8ч — можно по REST за период |
| OKX Liquidations | НЕ записываем | Нужно подключать live |

**Что сделать:**
1. **На 488 pump сделках где есть tape (~244 сделки):**
   - `pre_60s_taker_buy_ratio` — реальный delta перед входом
   - `pre_60s_cvd` — суммарный CVD за 60с
   - `signal_minute_buy_ratio` — на сигнальной минуте
   - `vol_clusters_pre` — есть ли крупные сделки за 5 мин до
2. **Найти топ-2 признака** где разделение TP vs SL максимально (например `pre_60s_taker_buy_ratio > 0.65` → WR 60%+ vs 30%)
3. **Симуляция фильтра:** "если signal не имеет нужный tape-pattern → SKIP". Сколько SL уйдёт, сколько TP пропадёт.
4. **Funding rate research:** через OKX REST history-funding-rate API (если возможно), забирать funding на момент каждого pump signal. Корреляция funding ↔ outcome.

### 3.4 Pair risk overrides (поглощает Brief #2)

**Контекст:** в `docs/gpt_audit_report_18_05_2026.md` ты сам предложил:

```yaml
pair_risk_overrides:
  BABY-USDT-SWAP: { size_mult: 0.0 }
  RIVER-USDT-SWAP: { size_mult: 0.0 }
  APR-USDT-SWAP: { size_mult: 0.5, require_breakeven: true }
  BSB-USDT-SWAP: { size_mult: 0.5, require_breakeven: true }
  BILL-USDT-SWAP: { max_trades_per_day: 2, ban_after_sl_streak: 2 }
```

**Главная гипотеза автора:** после внедрения breakeven trail (`mfe_r>=1.0`) **часть SL по этим парам станет BE-выходами**. Значит **pair_risk_overrides может быть избыточен или вреден** (зарежет потенциальные TP).

**Симуляции (минимальный набор):**

| Sim | Что меняется vs Sim0 (after-3-fixes baseline) |
|-----|------------------------------------------------|
| Sim0 | After-3-fixes baseline (breakeven, Path B off) |
| Sim1 | + BABY: size_mult=0.0 |
| Sim2 | + BABY + RIVER: size_mult=0.0 |
| Sim3 | + APR: size_mult=0.5 |
| Sim4 | + BSB: size_mult=0.5 |
| Sim5 | + BILL: max_trades_per_day=2, ban_after_sl_streak=2 |
| Sim6 | **ВСЕ overrides** |
| Sim7 | Только `ban_after_sl_streak=2` динамически для всех пар |
| Sim8 | + Tape-фильтр из 3.3 на всех парах |
| Sim9 | **Tape-фильтр (3.3) + минимальные pair overrides** — комбинация |

**Что ответить:**
1. Какие пары после breakeven перестают быть проблемными?
2. Какой override даёт max impact с min cuts (winners survival)?
3. Где overfit на 3 дня — какие правила НЕ надо вводить?
4. **Финальный вердикт:** нужны pair_risk_overrides если уже есть tape-фильтр?

**Расширить выборку до 03.05** через архив (`logs_archive/09.05.2026/pump/pump_labels.jsonl`) для validity check.

**Sample size warning:** 3 дня = 121 trade, маленькая выборка. **OOS test обязателен**: split train (16-17.05) → test (18.05) или train (03-13.05 архив) → test (14-18.05).

---

## Формат финального отчёта

`docs/gpt_full_research_18_05_2026.md` — один документ, структура:

```
# Full Research Report

## Executive Summary (макс 1 экран)
- 5-7 главных находок с numeric impact
- Топ-3 рекомендации с приоритетом

## Block 1: MAIN
  ## 1.1 By Regime
  ## 1.2 By Style (focus: SWING TRENDING fix)
  ## 1.3 Advanced parameters
  ## Block 1 Recommended Changes (с YAML diff)

## Block 2: BB FADE
  ## 2.1 Archive pattern mining
  ## 2.2 New algorithm validation
  ## 2.3 Recommendations (с YAML diff)

## Block 3: PUMP
  ## 3.1 Historical commit walk
  ## 3.2 Reapplied winning params simulation
  ## 3.3 Tape-based filters (the big one)
  ## 3.4 Pair-level final
  ## Block 3 Recommended Changes (с YAML diff)

## Integrated Action Plan
- Priority 1 (high impact, low risk): X
- Priority 2 (high impact, medium risk): Y
- Priority 3 (research, не делать сейчас): Z

## Data quality / coverage notes
- Sample sizes per bucket
- Missing data gaps
- Recommended data collection
```

## Constraints на финальный отчёт

1. **Numeric импакт обязателен для каждой рекомендации.** "Improves things" без цифр — не принимается.
2. **Risk callouts** — для каждой рекомендации: что может пойти не так?
3. **Implementation diff** — конкретный YAML diff к `config.yaml` или код diff к `scripts/ws/*.py` для каждой рекомендации
4. **Sample size disclaimer** — для каждого бакета указать n и confidence level

## Что НЕ делать

❌ Не пиши промежуточных писем "уточняю задачу"
❌ Не предлагай новые архитектуры (multi-agent, ML) — это Phase G в backlog
❌ Не делай рекомендаций без numeric simulation
❌ Не трогай production код напрямую
❌ Не теряй токены на форматирование таблиц — markdown простой текст ОК

## Что приветствуется

✅ Глубокие срезы, неочевидные паттерны
✅ Кросс-валидация: split по дате (train/test) для бакетов
✅ "Не подтверждается" находки — тоже важны
✅ Альтернативные интерпретации
✅ Sample size warnings честно

---

## 🔍 Post-report audit by Claude (важно знать)

После твоего отчёта Claude (другой AI в проекте) проводит **систематический аудит**:

### Что Claude будет проверять

1. **Воспроизводимость расчётов** — Claude **запустит ключевые симуляции сам** на тех же данных. Если численные результаты разойдутся >2 п.п. — запрос на пересчёт.
2. **Sample size honesty** — если рекомендация основана на n<20 без чёткого disclaimer "preliminary, requires more data" — пометит как недостаточно обоснованную.
3. **Logical consistency** — например если ты в Block 1 предложил отключить SWING TRENDING, а в Integrated Plan его оставил — Claude поймает.
4. **YAML diff проверка** — каждый YAML diff Claude **применит локально на копии config**, проверит что валидно (`python -c "import yaml; yaml.safe_load(open('config.yaml'))"`).
5. **Risk callouts adequacy** — если рекомендация имеет очевидный downside (например "blacklist BABY based on 5 trades") но он не упомянут — Claude добавит.
6. **No hidden assumptions** — если симуляция полагается на "tick order MFE→SL" а должно быть наоборот — Claude проверит на конкретных примерах.

### Что Claude НЕ будет делать

- ❌ Переписывать твои выводы
- ❌ Заменять твои recommendations своими
- ❌ Оспаривать дизайнерские выборы (например порог breakeven 1R vs 0.5R) если у тебя есть обоснование

### Что Claude **может попросить дополнить**

- Sample sizes в табличной форме (n / decisive / WR / 95% CI если посчитал)
- Конкретные сделки-примеры для top-3 рекомендаций (signal_id, timestamp, что было до/после)
- Альтернативные интерпретации для нестабильных находок

### Финальное решение по применению

После твоего отчёта + аудита Claude — **человек-автор проекта** принимает решения. Twin-AI review (ты + Claude) — рекомендация, не указание.

**Лайфхак для тебя:** пиши отчёт так, чтобы он **выдерживал аудит Claude**. То есть:
- Каждая цифра — с источником (path к файлу + строка кода или формула)
- Каждая рекомендация — с симуляцией numeric impact
- Каждая рискованная находка — с честным "n=X, низкая confidence"

Это сэкономит итерации back-and-forth между AI.

---

## Стартуй

Используй любые скрипты в `scripts/analysis/` для подготовительной работы. Создавай новые в `scripts/analysis/research/` если нужны.

Когда отчёт будет в `docs/gpt_full_research_18_05_2026.md` — закончил. План и бэклог переписываются после ревью.

Удачи. Это твоя самая большая задача в проекте.
