# CYCLE-1 запуск — день 3 (2026-05-28)

**Главное событие дня:** обнаружена **методологическая дыра в лейблере** (label_main_ws.py использовал
limit-fill семантику, прод okx_client.py:300 использует ordType="market"). После исправления и перелейбла
архива: **WR 55%→76% decisive, sumR −10.49→+8.81 R-multiple**. **Direction-эдж майна реален**, мы
2+ недели хоронили живой движок на фантомных числах.

## ❗ Корректировка предыдущих выводов

| Заявлялось ранее | Правильное число | Источник |
|---|---|---|
| «Main WS WR 56% после fill-gate» | **WR 76% decisive (TP vs SL)** | journal после перелейбла 28.05 |
| «sumR −10.49, main_ws ОТРИЦАТЕЛЬНЫЙ» | **sumR +8.81 (положительный)** | journal после перелейбла 28.05 |
| «31% сигналов NO_FILL» | **0% NO_FILL** (market всегда фильнётся) | label_main_ws.py market-entry |
| «Эджа в скальп-сетапе нет» | Direction-эдж реален; geometry/exit ещё минусит | tests + journal |

**Эти числа в `MEMORY.md` bullets 24.05-26.05 и в нескольких docs/ ПРЕДШЕСТВУЮТ фиксу. Считать их фантомом.**
Канонический источник — этот документ + журнал после `build_journal.py`.

## 1. Методологическая дыра (что было сломано)

`label_main_ws.py` лейблил так:
- BUY: позиция открывается ТОЛЬКО когда `low <= entry` (касание лимит-уровня); иначе NO_FILL.
- SELL: симметрично — `high >= entry`.

Прод-бот `src/exchange/okx_client.py:285-322` ставит `ordType="market"` — позиция открывается
сразу на следующем баре после сигнала, **никаких лимит-касаний**. То есть лейблер симулировал
лимитную семантику, а живой бот ходил маркетом. **Расхождение методологии и реальности.**

Fix-gate был добавлен 24.05 потому что без него засчитывались NO_FILL как победы (другой фантом).
Но фикс был перегибом в обратную сторону.

## 2. Что сделали 28.05

**Лейблер починен (`label_main_ws.py`, коммит `6a46188`):**
- Убран fill-gate
- Позиция открывается на первом баре после `signal_ts`
- SL/TP/TIME логика — как есть
- NO_FILL категория больше не возможна

**Архив перелейблен** (`scripts/analysis/research/relabel_archive_market.py`):
- 130 сигналов
- Старые метки сохранены в `main_signals_labels_legacy_limit.jsonl`
- Новые в `main_signals_labels.jsonl`

**Журнал пересобран** (`build_journal.py`):
- Main WS: 130 сигналов, **WR=62/82=76%**, **sumR=+8.81**
- BB Fade: 17 сигналов, WR=6/10=60% (не задет — там без fill-gate было)

## 3. Перепрогон 3 тестов на правильной семантике (приватный research-репо)

Все три скрипта (в приватном `trading-bot-research`) сами имплементили fill-gate. Перепрогнаны на market-entry.

### Test #2 — Exit-grid
- Скрипт: `scripts/analysis/research/exit_logic_grid.py`
- Holdout n: 23→**37** (+61%, NO_FILL отвалился)
- E0 baseline NET: −2.38%→**−1.51%** (улучшилось, но всё ещё минус)
- Лучший: E1 trail-1.5, NET **−0.64%** (Δ +0.87 vs E0)
- **Рекомендация:** НЕ внедрять в прод — все варианты в холдауте минус, главный leak = широкий SL на тонких альтах, не TIME-bleed.

### Test A — Trailing-ATR stress
- Скрипт: `scripts/analysis/research/exit_trail_stress.py`
- Sweet-spot mult: T1.0
- Baseline B NET: −2.38%→**−1.59%**
- **SWING ex-outliers Δ +0.86%/трейд** на n=8 (3× выше floor'a комиссии — реальный сигнал)
- BSB+EDEN dependency: 92%→76% (всё ещё доминирует)
- **Рекомендация:** ❗ СМЕНА с (d) «не подключать» → **(b) per-style SWING-only k≈1.0** — но n=8 мало, отложено в cycle-2 после форвард-валидации

### Test B — Polarity-flip
- Скрипт: `scripts/analysis/research/polarity_flip_test.py`
- Headline holdout: FLIP +0.75% vs ORIG −1.04% → Δ +1.78% **(но это 2 outlier-сделки BILL+EDEN)**
- Apples-to-apples (без outliers): ORIG slightly wins (Δ +0.08% в cost-шуме)
- Train: ORIG +0.022% > FLIP −0.304%
- Forward 3-bar в ORIG-направлении: 73% holdout, mean +0.25%
- **Вывод: polarity-flip ГИПОТЕЗА МЁРТВА.** Headline был фантомом NO_FILL-асимметрии. Направление бот выбирает правильно.

### Test #0 — Regime audit (был корректен, не задет)
- Скрипт: `regime_audit_universe.py`
- TRENDING на 24-bar окне реально mean_VR 0.938 (MR-поведение)
- **НО** входное окно бота (3-5 баров = 15-25 мин) короче окна VR-теста
- В короткой шкале направление ORIG корректное (73% сигналов идут в ORIG-сторону в первые 15 мин)
- **Уточнение:** regime-классификатор НЕ требует polarity-flip. VR<1 на 24-bar — характеристика более долгого окна, не входного.

## 4. Cycle-1 пакет правок (коммит `533e69d`)

| # | Правка | Файл | Обоснование |
|---|---|---|---|
| 1 | Лейблер market-entry | `scripts/analysis/label_main_ws.py` | Соответствие проду |
| 2 | `main_screener.top_n_pairs` 24→60 | `config.yaml` | Покрыть весь альт-юниверс (боль «не все пары») |
| 3 | Убран `di_spread_4h≥8` | `src/strategy/signal_engine.py:1018` | Test #1 V1 = V0 (мёртвый гейт) |
| 4 | Убран `_bb_ok` (SWING+FAST) | `src/strategy/signal_engine.py:1018,1028` | Test #1 V5 = +6 сигналов на 1581 (мёртвый) |
| 5 | BB Fade отключён | `start_all.bat` | Фокус только Main для чистоты наблюдения |

**Отложено в cycle-2 (после 10-дневного наблюдения):**
- Pair-filter катастрофических SL (BSB/EDEN/MEW/BOME/USELESS) — требует понимания screener-юниверса
- SWING trailing-ATR k=1.0 — требует динамической логики + OKX API, n=8 на тестах мало

## 5. Что запущено и пишет данные

| Процесс | Файл | Лог |
|---|---|---|
| Telegram Bot | `scripts/telegram_bot.py` | `logs/telegram_bot.log` |
| Main Screener (shadow) | `scripts/ws/ws_main_screener.py` | `logs/ws_main_screener.log`, сигналы в `logs/signals/main_signals.jsonl` |
| Live Screener | `scripts/ws/ws_screener_live.py` | `logs/ws_screener_live.log`, мониторит ~236 пар |
| Tape Recorder | `scripts/analysis/tape_recorder.py` | `E:\trading-data\ticks\<SYM>\<DATE>.csv.gz` |

**AUTO_TRADE=false ✅ (money-guard цел).** Запуск через `start_all.bat`.

## 6. 10-дневный цикл наблюдения

**Цель:** собрать чистый набор сигналов с фиксированным лейблером и cycle-1 правками. Не трогать прод.

**Что собираем:**
- Сигналы Main WS на новых данных → `logs/signals/`
- Тиковые цены → `E:\trading-data\ticks\`
- Telegram-уведомления (для трекера)

**Что в конце цикла (~07.06.2026):**
- Запустить `python scripts/build_journal.py` — увидим реальный WR/NET за 10 дней
- Сверить с архивным baseline (WR 76% / sumR +8.81)
- Принять решение по cycle-2 правкам:
  - Pair-filter если катастрофические SL появились
  - SWING trailing если SWING-сигналов накопилось достаточно для статзначимости

**Что НЕ трогать 10 дней:**
- `config.yaml`, `signal_engine.py`, `start_all.bat`
- Не запускать `stop.bat` без необходимости

## 7. Артефакты

- Public репо (`trading-bot-v2`):
  - Этот документ
  - Лейблер фикс `scripts/analysis/label_main_ws.py` (6a46188)
  - Cycle-1 правки (533e69d)
- Приватный репо (`trading-bot-research`):
  - `relabel_archive_market.py`
  - `exit_logic_grid.py` (обновлён под market-entry)
  - `exit_trail_stress.py` (обновлён)
  - `polarity_flip_test.py` (обновлён)
  - `regime_audit_universe.py`
  - `docs/{exit_logic,exit_trail_stress,polarity_flip}_results_2026-05-28_market.md`

## 8. Ключевой урок

**Перед тем как делать выводы об «эдже нет / эдж есть» — ПРОВЕРИТЬ что симулятор/лейблер соответствует проду.**
2+ недели мы хоронили работающий движок из-за расхождения симуляции и реальности. Этот урок дороже любого
найденного эджа.
