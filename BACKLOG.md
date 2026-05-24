# BACKLOG — Идеи не для текущей фазы

Сюда записываем всё что "хорошая идея но не сейчас".

**Правило ревью:** Claude проверяет этот список каждые 2-3 дня в начале сессии.

**Последнее ревью:** 2026-05-24 — **импульс ЗАКРЫТ (forward NO-GO)**, оба движка off + архив; воркфлоу-наладка (карусели/.claude); фокус → geometry-research

**Условные обозначения:**
- ✅ Внедрено в прод
- 🧪 Протестировано, отклонено (с причиной)
- ⏳ Запланировано, не начато
- ❌ Закрыто (устарело / нет смысла)

---

## 🎯 Research 18.05.2026 — что применяем СЕЙЧАС

Источник: `docs/gpt_full_research_18_05_2026.md` (полный отчёт), аудит пройден.

### ✅ Pump: session_ban_sl_no_tp 3 → 2 (применено 18.05)
- Бан пары после **2** последовательных SL вместо 3
- Effect на 2026-05-16..18 backtest: **+4.75 п.п.** net
- Применено в `config.yaml` → `pump_orchestrator.session_ban_sl_no_tp: 2` (commit 84ec5e1)

### ✅ Pump: pair_risk_overrides + hard-block APR/RIVER/LAB (применено 18.05)
- Секция `pair_risk_overrides` в config + обработчик в движке
- APR / RIVER / LAB → mode: block (нет tape coverage, net -14.60% на 11 сделках)
- BSB / BABY / BILL — не блокируем (есть tape coverage)
- **Эффект Sim8:** +16.27 п.п. на 3-day window (commit 564d58e)
- **Примечание (19.05):** ws_pump_orchestrator.py АРХИВИРОВАН. Оба фикса перенесены как risk-containment в ws_smart_pump.py (новый движок).

### ❌ НЕ применяем сейчас (отложено)
- **Sim9 full overrides** (BABY/BSB/BILL hard block + BSB half-size + BILL cap2) — overfit risk на 3 днях слишком высокий, +24.81 п.п. может стать -X на следующей неделе
- **main scanner config откат** (`min_vol_ratio_trending`, `prefilter_*`) — ждём результаты majors-vs-alts эксперимента (GPT Path A)
- **BB Fade config** — live n=3, no signal

### 🔬 Main scanner research — выводы 19.05 (`docs/gpt_majors_vs_alts_19_05_2026.md`)

Path A симуляция + декомпозиция conditions_not_met завершены. Ключевые находки:

**Что подтвердилось:**
- TRENDING×SWING регрессия реальна на majors тоже (24→2, avg_R: +0.09→-0.32) → scenario B
- DRIFT×FAST и TRENDING×FAST — **precision-positive**: cuts много но WR/avg_R растут (DRIFT×FAST 75.5%→91.3%, +0.06R→+0.27R) → фильтры работают корректно
- Чтобы восстановить TRENDING×SWING нужны **ДВЕ независимые правки** (overlap=0):
  - `min_vol_ratio_trending=1.5` — режет 12 trades (5 TP среди показанных)
  - `slope_min=35` + `adx_1h_rising` strictness — режет ещё 8 trades (6 по slope, 2 по adx_rising)

**Что НЕ делаем (решение 19.05):**
- Прод main scanner **не трогаем сейчас**
- Угадывать "одним фиксом" не получится (overlap=0, две хирургии)
- Изменение в неделю сильнее ломает чем чинит — пусть текущий стек работает

**Точечные действия отложены до:**
1. Накопления **live данных по майорам** (нужны pinned_pairs или ждать пока сами проскочат в top_n)
2. Полного исторического реплея на 1m свечах с честным построением levels (3-5 дней работы GPT)

**Следующий research-шаг (не сейчас, не блокирующий):**
- Прицельный majors-only replay для `min_vol_ratio_trending` (изолировать эффект)
- Pinned_pairs в config — добавить BTC/ETH/SOL/XRP/DOGE в universe для накопления live (low risk, no code changes if уже поддерживается)
- Когда соберём 20+ live TRENDING×SWING на майорах с текущими фильтрами → принимать решение по правке

**Записано как research-track, не как немедленное действие.**

### ✅ Применено ранее (commit 9d4efa5, до research)
- ✅ Telegram delivery fix (load_dotenv() в orchestrator) — msg доходят
- ✅ Path B disabled (`enable_path_b: false`) — обход 2nd-candle confirmation выключен
- ✅ Breakeven trail (`breakeven_trail_enabled: true`, `breakeven_trigger_r: 1.0`) — SL → entry при MFE>=1R

---

## 🧭 Приоритеты на 2026-05-23

> Сверять с этим разделом в начале каждой сессии. Менять при закрытии фаз / появлении блокеров.

### 🛑 24.05 ВЕЧЕР — СТОП-ПОЙНТ + развилка на завтра (25.05)
Лейблер починен (фантом-филлы убраны) → честно: **у живых каналов (Main WS, BB) доказанного эджа НЕТ**
(WR 56% не 75%, main_ws sumR +7.38→−10.49, 31% сделок = NO_FILL). Геометрия выхода на честных филлах минусит;
прежнее «V3 +0.4%/плато» — фантом. Корень — **механика входа** (откатная лимитка адверс-селектит). Детали `docs/geometry/findings.md`.
**🔀 РЕШИТЬ ЗАВТРА:** (1) тест #1 market-entry (есть ли direction-эдж в сигнале) — read-only, рекомендовано;
(2) стратегразговор (механика входа / смена сетапа). Прод/деньги не трогаем (paper).

### 🔴 24.05.2026 — ИМПУЛЬС ЗАКРЫТ (forward NO-GO) → фокус geometry-research
Оба новых движка остановлены и заархивированы (`docs/strategy_impulse_postmortem.md`): `ws_impulse_pump`
−19.67%/WR14%, `ws_main_impulse` −7.77%/WR0% за ~28-40ч. Research +3%/84% не пережил форвард (replay↔live gap,
BSB-оверфит, capture~0). Логи сохранены — это датасет для geometry.

**Решение трейдера:** не чинить 5 движков. Глушим бледеры; живые каналы = **Main WS** (тонкий +0.11R) + **BB Fade**.
**Вывод дашборда журнала (все вкладки):** WR ок на ВСЕХ каналах (37-75%), деньги ~0/минус везде → корень не
направление/канал, а **геометрия / риск-на-сделку (стоп/тейк/сайзинг).**

- **🔬 СЛЕДУЮЩИЙ ФРОНТ — geometry-research:** на логах импульса/BB (есть MFE/MAE/capture) измерить, насколько стоп
  великоват к движению и сколько съедает выход. **НЕ запускать новые движки** — решать геометрию для существующих.
  Сюда ложится entry-precision кластер из каруселей (см. «🎠 Карусели» ниже).

### 🎥 Видео-ресёрч 24.05 — промоут из инфополя
Разобрано 15 роликов (пачка 23-24.05). Полный реестр + вердикты: `docs/video_research_catalog.md`.
Сюда промоутнуто только actionable/направленческое; скип (Эллиотт ×3, MT4-промо, дубль) — в реестре.

- **🟡 trade — кандидат на бэктест:** кластер «**4H-якорь + реклейм**» (тема10 первая 4H-свеча NY →
  фейд ложного пробоя R:R 1:2; тема22 4H-направление + снятие ликвидности + ретест/ICT). Механизируемо,
  данные/харнесс есть. **Флаги:** фикс RR + тесный стоп (та геометрия, что убивала майн), чувствительность
  к комсе, пересечение с BB Fade и «ночным стоп-хантом». → гонять одним честным бэктестом, НЕ в лайв.
- **📦 Отдельные направления** (см. раздел «Отдельные проекты»): Марченко–Пастур (чистка ковариац-матрицы,
  портфельная оптимизация); стат-арбитраж/парный трейд (OU, Z-score) — пересекается с арбитраж-идеями ниже.
- **🔬 dev/AI для стройки** (к фазам): Paperclip + FireCrawl + DeepSeek-дешёвый-слой + Obsidian-память →
  Phase G; Supabase-стек → Phase S3.1. Это **референс к паркованным фазам**, не текущая работа.

### 🎠 Карусели 24.05 — промоут из инфополя
Проход 2 (детальный) по `docs/carousel_catalog.md`. Сюда — только actionable; awareness-гайды «как юзать Claude» не плодим.

- **🟡 trade (точность ВХОДА, не «ещё направление»):** SMC-кластер — imbalance/FVG (#42), ордер-блоки (#48),
  smart-money интро (#50), структура разворота (#56) + пробой-на-сжатии-волатильности (#69) + вход/SL после
  уровня (#58). Рассматривать как research **точки входа для импульс-движка** (поздний вход = раздутый стоп =
  убитая геометрия). Ложится в **context_gate Phase 2** (микроструктура в момент сигнала, см. P2). Оговорки:
  дискреционно — механизировать объективно; та же геометрия (фикс RR / тесный стоп), что валила майн.
- **🟡 trade:** ORB сессионный (15m ORB + VWAP, #34/#36) — рядом с «4H-якорь + реклейм». Один честный бэктест, не в лайв.
- **📦 квант (далёкий горизонт):** #61 ML+Finance проекты (нейросеть на OHLC, vol surface, yield-curve forecast),
  #62 «7 квант-стратегий для домашнего бэктеста» → зона квант-ресёрча, рядом с Марченко–Пастур + стат-арб (выше).
  Референс, не работа сейчас.
- **🔬 LLM-тема (Phase G):** #49 parallel multi-agent crypto-analyst (LangGraph fan-out→fan-in:
  Price/Sentiment/OnChain/Macro/Risk → Synthesis → BUY/HOLD/SELL) = **почти буквально Phase G.3**, лучший
  референс-материал; #41 self-healing RAG (grade-нода, anti-hallucination). Cross-ref Phase G, deep-read когда дойдём.
- **⚙️ dev-воркфлоу (это работа ТЕКУЩЕГО окна, не бэклог):** `.claude/` setup (settings allowlist + SessionStart hook
  + PreToolUse money-safety guardrail на AUTO_TRADE/.env) из каруселей #22/#29/#57. Обсуждается отдельно.

### ✅ СДЕЛАНО 23.05 (вечер) — МАЙН research проведён ЦЕЛИКОМ + аудит ядра
Прошли весь research-цикл локально (скрипты `main_rebuild_*_23_05_2026.py`, документы `core_audit_*`,
`architecture_vision_human_*`). Главные выводы (перевернули прежнюю премису бэклога):

- **Карта режимов (доказано цифрами):**
  - **ИМПУЛЬС (TRENDING_IMPULSE) = ✅ ЕДИНСТВЕННЫЙ доказанный эдж.** Ранний вход + scaled/ride выход:
    in-sample +0.48% (n=55, bootstrap CI [+0.21,+0.76]%, p=0.001, обе стороны, не fat-tail), **walk-forward
    OOS 45д: 5/5 окон net+, +0.41%** (n=234). GPT real-tick 17-23.05: +3.02% (с оговоркой shuffle/window).
  - **GRIND (плавный тренд, 78% всех событий!) = ⛔ МИРАЖ.** «Ride долгого тренда» +1% оказался **look-ahead**
    (`event["direction"]` берётся из БУДУЩЕГО). Честное направление → −0.46%, WR 25%. Эджа нет.
    **Урок: `event["direction"]` = future-метка, НЕ сигнал.**
  - **RANGING = 🟡 чинибельный.** Направление 72% (хорошее!), минусит ГЕОМЕТРИЯ → фейд-research.
  - **DRIFT = ⛔ слабый.** В коде **long-only** (`drift_short_veto`), движение крошечное → деньги ~0. Дроп.
- **Премиса «DRIFT×FAST WR 91% работает» УТОЧНЕНА:** продовое НАПРАВЛЕНИЕ ок (decWR 67-85%), но **ДЕНЬГИ ~0
  во всех режимах** (WR-вэнити: крошечный TP). «24% direction» из ранних аудитов = артефакт реимпла, снято.
- **Архитектура согласована** (`docs/architecture_vision_human_23_05_2026.md`): классификатор → 3 пер-режимных
  анализатора (свои неймспейс-параметры, FAST/SWING как ось УБРАНА) → торговый оркестратор-исполнитель.
  Памп = отдельный 4-й цех. 3 инварианта против коллизий.
- **Аудит ядра (`docs/core_audit_23_05_2026.md`, 11 находок):** B1 баг `round(x,4)` ломает дешёвые монеты
  (⅓ юниверса) → day_position/vwap мусор; B2 хардкод символов в src/; B4 DRIFT long-only; B5 12+ вето (recall);
  B6 геометрия (корень «деньги ~0»); B7 проверить лаг свежести live (фид хранит только закрытые свечи + `[-2]`);
  signal.py — мёртвый код. GPT независимо подтвердил + 12 код-уровневых коллизий.

**✅ СДЕЛАНО (поздний вечер 23.05):** B1 исправлен; **импульс paper-движок собран** (`ws_main_impulse.py` +
`main_impulse_*` + `signal_contract.py`), **аудит Claude зелёный**, влит в main + запушен, ветки почищены (одна main).
Аддитивно (старый майн/LLM/журнал целы), снапшот/LLM несут `exit_rule` (ride). Уведомления → общий памп-чат, метка
«🔵 МАЙН». Подвязан в start_all/stop, **запущен на форвард-paper** (8 пар; памп тоже бежит → A/B на тех же парах).

**▶ СЛЕДУЮЩЕЕ:** наблюдать форвард ~неделю / накопить **~100 сигналов** в `logs/main_impulse/`. ЕСЛИ эдж держится
(net+ обе стороны) → **context_gate Phase 2** (микроструктура из тиков в момент сигнала — см. P2 ниже) + замена старого
майна (LLM/Telegram на новый движок) + пенсия старого. ЕСЛИ минус (как памп −11.5% день 1) → не отдаём клиенту,
разбор replay↔live. Остаток аудита (B2 хардкод, B5 вето) — при доведении. Фейд (RANGING) — отдельный research.

### 🔴 ПАМП paper — ДЕНЬ 1 КРАСНЫЙ ФЛАГ (23.05)
Запущен ~04:15. **8 сделок: net −11.51%, WR 25% (2/8), средн −1.44%/сделка.** Против research +3.03%/84%.
**BSB (звезда research +3.68%) — главный убийца дня** (4 сделки, 3 SL по −2.5…−3.6%). Replay GPT +3.02% vs
live −11.5% → **разрыв replay↔live реален.** n=8/один день — мало, но красный флаг. Решение трейдера:
держать испыт. срок до недели (n копится) или пауза. **Денег НЕ трогаем (paper, AUTO_TRADE=false).**

### ✅ Сделано 23.05 — памп реструктуризирован в paper-движок «рывок» и ЗАКРЫТ
- **Рабочий edge (polish 22.05):** «рывок» = ранний ТИКОВЫЙ вход на импульсе волатильных альтов + структурный ride. **+3.03% net, WR 84%, обе стороны, capture ~50%**, BSB +3.68%/EDEN +2.10%. Это памп, сделанный правильно (старый reversal мёртв). ⛔ Испыт. срок: ~2-3 дня тиков, BSB fat-tail → forward-paper подтверждает на 2-3 недели. **Денег НЕ трогаем.**
- **GPT собрал** `ws_impulse_pump.py` + `src/data/impulse_pump_*.py` (PAPER-only, замороженный конфиг в `config.yaml:impulse_pump`, enabled:false).
- **Аудит Claude пройден** (`3351faf`): paper-only гарантирован, ордеров нет, конфиг = research, `--check-config` ок.
- **Training-grade записи** (`impulse_pump_records.py`): сигнал+исход+training с valid-флагом → `logs/impulse_pump/`.
- **Подвязан в start_all/stop** (`af360f7`); **журнал — вкладка «Импульс»** (`31889a3`, метрика net_pct, не TP/SL).
- **Запуск paper** (когда решит трейдер): enabled:true + `.env` AUTO_TRADE=false + PUMP_CHAT_ID + start_all → метрики в journal.xlsx.

### ✅ Сделано 22.05
- **Phase B Б-Д + exit re-run + 3 движка + polish.** Вывод: внутри 3 РАЗНЫХ зверя (импульс/тренд/диапазон), мерили одной импульс-линейкой → всё выглядело мёртвым.
- **edge_exists vs capture:** движение ЕСТЬ (TRENDING_IMPULSE 82%), ловили ~25% — корень в ИСПОЛНЕНИИ (поздний вход + тесный TP), не в отсутствии эджа.
- **Рывок (ранний тик-вход) найден** — первый сильный плюс за месяц. Тренд: продовый = ответ (реимпл хуже). Фейд: близко (net~0), тонко.

### 📦 ПАРКОВАНО
- **Фейд (BB, диапазон)** — net ~0, выборки тонкие (high-vol opposite +0.21%, n=27); дошлифовать когда дойдём.
- **Обучающая БД** — `flag_invalid_signals.py` (база); training-записи строятся в новом памп-движке. Свести main+BB+pump в один формат позже.
- **Накопление S2.3 labeled** — копится пассивно.
- **Phase G.0 forensics / Training DB** — после.

### 🟢 P2 — research candidates (записать, не делать)
- **context_gate Phase 2 для main screener** — tape enrichment в момент сигнала (trade_delta, OBI, spread). После 100+ labeled сигналов, пока не трогаем.
- **Main scanner на 5m триггерах** — сравнить 15m vs 5m триггеры на одной истории. Часть G.0 research.
- **Pinned majors в universe** — BTC/ETH/SOL/XRP/DOGE always-on в ws_main_screener для накопления TRENDING×SWING данных по майорам.

### ⚪ P3 — большой горизонт
- Phase G.1-G.4 multi-agent LLM
- Phase S3.1 Desktop Journal App
- Telegram Dev-Bridge

### Что НЕ начинаем сейчас
- Новые фичи в ws_smart_pump пока нет 50+ сделок
- Откат main scanner фильтров без данных
- Параллельный старт двух новых треков

---

## ✅ ТЕКУЩИЙ ЛУЧШИЙ БЭКТЕСТ (эталон)

**Дата:** 2026-05-03 (D2+B3)
**Git commit:** `91c1807` (движок) / `86fb111` (HEAD)
**Период:** 63 дней, 5 пар (BTC/ETH/SOL/XRP/DOGE) + ADA кэш

### DRIFT с D2+B3 фильтрами:
| Метрика | Значение |
|---|---|
| Сигналов | **146** |
| WR | **89%** |
| Profit Factor | **3.51** |
| Симуляция | **+144.1%** |
| Макс. просадка | **6.3%** |

### История улучшений:
| Изменение | Коммит | Эффект |
|---|---|---|
| V6C ranging_recovery | c2262fd | sim +90%→+105%, +9 сигналов |
| FAST hold 150m→90m | 73278a4 | sim +107%→+114%, DD 5%→4% |
| BB FADE not_thrust+slope_fading | 088cd7b | фикс P1 (5 убыточных DOGE) |
| D2: trigger vol < 0.9× baseline | 91c1807 | DRIFT WR 74%→89% |
| B3: ETH veto UTC 22-01 | 91c1807 | DRIFT n 158→146 |

### Параметры прода:
- FAST hold: **90m** (240m ночью), SWING hold: **300m**
- SL: `sl_k × 1.2 × ATR_15m`
- FAST TP1: 0.8R (TRENDING/RANGING) / 0.4R (DRIFT)
- ADX period: **9** (из config.yaml)
- BB FADE пары: BTC, ETH, DOGE (XRP/SOL отключены — PF < 1)

---

## P1 — Pump Engine: профиль входа для ресурсов и акций

### ⏳ Отдельный профиль входа для commodities/stocks в pump engine
- Скринер уже мониторит CL-USDT-SWAP (нефть) и другие non-crypto SWAP пары
- Текущие пороги входа (1m vol_spike + price_move) настроены под крипто-альткоины
- Нефть/золото/акции двигаются медленнее — нужен отдельный профиль: другой таймфрейм (5m/15m), другие пороги
- **Когда:** после стабильной статистики на крипто (Phase C pump engine)

---

## P0 — Ближайшие практические задачи

### ✅ BB Fade как отдельный WS процесс (ws_bb_fade.py) — 15.05.2026
- MTF BB Fade: 15m setup (BB touch) + 5m entry (wick rejection)
- Бэктест v3: WR=70.6%, avg=+0.478%, PF=1.89, 344 сигнала, MIN_WIDTH=2.0%
- Фильтры: RSI sell≤60/buy≥40, vol<1.5, bw≥2%, skip Asia UTC 00-06
- Запущен в start_all.bat, stop.bat, сигналы в Telegram + logs/bb_fade/
- Лейблер: scripts/analysis/bb_fade_label_outcomes.py

### ⏳ BB Fade: интеграция режимных правил (Phase F.2+)
- ws_bb_fade.py запущен как отдельный процесс ✅
- Следующий шаг: весовые правила RANGING/TRENDING/DRIFT; DRIFT — уменьшить TP1
- **Когда:** после накопления 50+ live сигналов из ws_bb_fade.py

### ⏳ BB Fade: tape pre_buy_ratio фильтр (Гипотеза F.2)
- Анализ bt_bb_tape_analysis.py: pre_buy_ratio 0.5-0.7 → WR=75%; <0.3 или >0.7 → WR=0%
- Данные: 5-мин окно перед входом из E:\trading-data\ticks\
- Нужно: tape_recorder копит данные → после 30+ дней данных проверить гипотезу на live сигналах
- **Когда:** когда tape_recorder накопит 30+ дней данных (ожидание ~июнь 2026)

### ⏳ Переработка пользовательской системы (LLM промт + Telegram UI)
- Текущая проблема: LLM не знает о режимах (TRENDING/DRIFT/RANGING), два параллельных источника сигналов, мёртвый `_scanner_loop()` в telegram_bot.py
- Что нужно: новый режим 5 для BB FADE в промте, осознание TRENDING/DRIFT в тексте, чистый Telegram UI без legacy кода
- **Зависит от:** ws_main_screener проработал 24-48ч в shadow-режиме → есть что оценивать
- **Когда:** следующая сессия после анализа логов main_screener

### ✅ Pump Engine: alt-coin пары (решено через новую архитектуру 19.05)
- ws_smart_pump.py работает на BILL/JELLYJELLY/NOT (WR reversal 54-63% по research)
- Не зависит от active_universe.json — всегда подписан на eligible_pairs
- Если нужно расширить universe → отдельный research на новых парах по той же методике (WS reversal universe scan)

### ⏳ analyze_signal_log.py — полный прогон
- Скрипт написан, но нужно 100+ labeled сигналов
- **Когда:** как только накопится достаточно данных

---

## P0 — Технический долг из аудита 16-17.05

### ✅ bt_pump_*.py архивированы (19.05.2026)
- 11 файлов (bt_pump_core, sweep, filters, equity, walkforward, hours, tp_sl, report, bt_tape_analysis, pump_live_report) → `scripts/archive/`
- ws_pump_orchestrator.py тоже архивирован — заменён ws_smart_pump.py
- Зависимость на production устранена ранее (fetch_ctvals в src/exchange/okx_meta.py)

### ⏳ CB state persistency в ws_smart_pump
- `session_ban_sl_no_tp` счётчик и `cb_halted` хранятся только в памяти
- После рестарта (watchdog) защита обнуляется
- Нужно: сохранять state в `logs/pump/smart_pump_state.json` + читать при старте
- **Когда:** до перехода в Phase D (real trading) — обязательно

### ⏳ Screener-to-pump silence alert
- Если ws_screener_live упадёт — ws_smart_pump живёт без обновлений active_universe.json (хотя eligible_pairs независимы, это всё равно признак проблемы)
- Нужно: heartbeat-check на возраст файла; если >5 мин → log WARNING + Telegram alert
- **Когда:** до Phase D

### ⏳ _calc_rsi в ws_bb_fade — слишком короткое окно
- Сейчас берёт `closes[-(period*3):]` для RSI(14) — это 42 свечи
- Wilder smoothing стабилизируется за 5×period = 70 свечей
- Результат: первые RSI значения смещены
- Решение: использовать `src.strategy.indicators.calc_rsi` на полном буфере
- **Когда:** после 50+ BB Fade live сделок (если WR не дотягивает до 65%)

### ⏳ Документация: пути backtest_simulate.py
- docs/BACKTEST_ENV_REFERENCE.md, docs/drift_test_map.md ссылаются на `scripts/backtest/backtest_simulate.py`
- Реально файл в `scripts/archive/backtest_simulate.py` (был перенесён)
- Нужно: либо обновить пути в docs, либо вернуть файл (зависит от того, нужен ли он сейчас)
- **Когда:** при следующем запуске бэктеста — кто-то наткнётся

### ✅ Tech audit + dead code archived (16.05.2026)
- v1/v2 pump engines → scripts/archive/
- pump_engine: секция config.yaml — DEPRECATED
- 6 хардкодов в оркестраторе → config (pending_ttl_sec, confirm_vol_min_ratio, stagnation_*, eviction_*, ban_hours)
- _check_position_live, expire_sec dead code — удалены
- src/exchange/okx_meta.py — fetch_ctvals вынесен из bt_pump_filters
- Commits: 0d0d38f, 1b31ab5

### ✅ Telegram rate-limit fix (17.05.2026)
- Проблема: 293 NOTIFY ok в группу за ночь → физически доставлено только 1 (Telegram silent drop при бурсте)
- Фикс: per-chat asyncio.Queue + worker с 2с min interval; send_message_to проверяет ok field + retry на 429
- Архитектурное: pump шлёт ТОЛЬКО в группу (extra_notify_chats), личные чаты убраны (личка зарезервирована за анализатором)
- Commit: 06503f5, 1b31ab5

---

## P1 — После закрытия B.5 + S2.3

### ⏳ Оркестратор основного сканнера
- По аналогии с pump orchestrator: сигнал → auto-open → live SL/TP мониторинг каждую секунду → auto-close
- Сейчас: сигнал уходит в Telegram → человек входит вручную → выход вручную. Никакого автоматического мониторинга нет
- Что нужно: отдельный `ws_signal_executor.py` — читает сигналы из main_signals.jsonl, открывает позиции, мониторит через _on_candle_update, закрывает по SL/TP
- AUTO_TRADE=false → режим paper (уже есть в auto_execute.py), позже true
- **Когда:** после того как ws_main_screener shadow → prod (закрытие S2.3) + WR подтверждён на 100+ сигналах

### ⏳ Data Recording System (архитектура готова — GPT 11.05.2026)

**Ключевой инсайт:** `compute_signal()` уже вычисляет всё (ADX/slope/BB/OBI/engine_vars по всем TF) — `ws_main_screener._maybe_emit_signal()` просто выбрасывает 80% при записи.

**Три компонента по приоритету:**

1. **Signal Snapshot** (быстро, ~20 мин) — добавить `json.dumps(result)` в `_maybe_emit_signal()` → `logs/signals/signal_snapshot.jsonl`. Единый `signal_id` для signal → snapshot → label.

2. **Per-candle Feature Log** (средне) — `src/data/feature_writer.py`, хук в WSFeed на закрытие 5m/15m/1H/4H, плоский CSV → `logs/features/{tf}/{symbol}/{date}.csv.gz` + `_index.jsonl`.

3. **Tick Recorder на HDD** (тяжело) — переработать `scripts/analysis/tape_recorder.py`: путь из `.env` (`TAPE_DATA_DIR=D:\trading-data\ticks`), per-symbol файлы, 30 пар, исправить `start_tape.bat` (неверный путь к скрипту).

4. **analysis_query.py** — `scripts/analysis/analysis_query.py`: берёт `signal_id` → находит snapshot → per-candle features ±30m → ticks ±5m → один DataFrame для анализа. Поддерживает фильтры: `--where "regime=='DRIFT' and outcome=='SL'"`.

**Новые файлы:** `src/data/feature_writer.py`, `src/data/snapshot_writer.py`, `scripts/analysis/analysis_query.py`
**Изменить:** `signal_engine.py` (расширить 4H индикаторы), `ws_main_screener.py`, `ws_scanner.py`, `tape_recorder.py`
**Когда:** после закрытия B.5 + S2.3, первым делом — Signal Snapshot (минимум кода, максимум пользы)

### ⏳ WS тестер — прогон WS-архитектуры на истории
- Текущий backtest_simulate.py работает на REST свечах (batch). WS-движок (ws_main_screener) тестируется только в лайве
- Идея: построить WS-тестер который воспроизводит WS-поток на исторических данных → прогоняет signal_engine → выдаёт те же метрики что и backtest_simulate.py
- Ценность: можно быстро тестировать изменения в signal_engine без ожидания 24-48ч лайв данных
- GPT уже в проекте — можно ему поставить задачу построить ws_backtester.py
- **Когда:** после закрытия S2.3, параллельно с оркестратором основного сканнера

---

## P1 — После замены основного сканнера на теневой (ws_main_screener)

### ⏳ Переработка LLM промтов и правил под новую архитектуру
- Текущие промты (llm_formatter.py) заточены под старый REST сканнер
- При переходе на ws_main_screener: новые поля (regime, FVG, vol_ratio, detected_on), новые стили (BB_FADE)
- Пересмотреть правила форматирования под каждый стиль: FAST/SWING/BB_FADE
- **Когда:** сразу после решения о переключении на ws_main_screener

### ⏳ Переработка клиентского бота под новый поток сигналов
- Текущий Telegram бот ожидает формат старого скринера
- Новый скринер даёт больше контекста: режим, таймфрейм, FVG, стиль
- Обновить: шаблоны сообщений, кнопки обратной связи, история запросов
- **Когда:** одновременно с переработкой LLM промтов

---

## P2 — Telegram канал ридер (Telethon)

### ⏳ Скрипт чтения Telegram каналов для сбора торговых идей
- Библиотека: Telethon (Python, Telegram API)
- Каналы: True_Market_Vision, MidChart, EuphoriaHL, Sokolov_TTFM, web3memoriess, uiartemzvezdin, Nat_Selection, O4racta1
- Сохранять посты локально → анализ паттернов и идей
- **Когда:** после закрытия Phase C (WR>60%)

---

## P1 — Ночной стоп-хант паттерн (Asian session)

### ⏳ Ликвидационный стоп-хант 00:00–06:00 UTC
- Паттерн: резкий памп/дамп на 15m в азиатскую сессию (низкая ликвидность)
- BTC и альты делают ложный пробой BB → собирают стопы → разворот
- Часы: 00:00–03:00 UTC (03:00–06:00 МСК) и 21:00–00:00 UTC (00:00–03:00 МСК)
- Торговая идея: ждать пика/дна движения → войти на BB reverse → TP середина
- Связан с BB Fade концепцией но специфичен по времени суток
- **Нужно:** бэктест по часам UTC на 15m данных за месяц, минимум 30 паттернов
- **Когда:** после Phase C pump engine

---

## P1 — BB Fade переосмысление

### ✅ BB Fade как самостоятельный скальп — реализован 15.05.2026
- ws_bb_fade.py: MTF 15m+5m, WR=70.6%, PF=1.89, запущен в прод (paper)

---

## P1 — После 100+ labeled сигналов

### ⏳ ETH-specific recalibration
- ETH системно слабее (B3 фильтр уже добавлен для DRIFT часы 22-01)
- Проверить: нужен ли дополнительный ETH-specific порог ADX или vol
- **Когда:** после полного прогона analyze_signal_log.py

### ⏳ Late Momentum Entry Filter
- Источник: SOL SELL 01:46 UTC — вход на хвосте состоявшегося импульса
- Гипотезы: distance_to_tp_consumed_pct, bars_since_trigger, price_extension
- **Когда:** 50+ TIME_EXIT кейсов → проверить корреляцию с late-entry

### ⏳ 1m Micro-Range Exhaustion Detector
- Диагностика протухшего импульса по поведению 1m цены после входа
- Метрики: 1m_range_width_10m, bars_crossing_entry, new_extreme_in_direction
- **Когда:** после late momentum filter research

### ⏳ Сессионный анализ WR по времени суток
- Группировка по hour_utc → таблица WR/PF/сигналов по часу
- Если паттерн есть → session_filter в compute_signal
- **Когда:** 100+ labeled сигналов

### ⏳ Daily Stop Limit
- Стоп после N стопов подряд за день, возобновление следующий UTC-день
- Счётчик consecutive_stops в scanner loop
- **Когда:** вместе с Reentry Guard (P3)

### ⏳ Публичный канал-отчётник со статистикой
- Telegram read-only канал: WR по парам/стилям, дневной итог
- Данные из signal_labels.jsonl — только закрытые позиции
- **Когда:** 100+ labeled сигналов — есть что показывать

---

## P1 — Signal Quality Research

### ⏳ SOL/ETH lead-lag correlation filter
- Наблюдение (10.05.2026): ETH даёт сигнал на 1-3 свечи раньше SOL при коррелированных движениях
- Идея: сигнал ETH (TRENDING/RANGING в одну сторону) → early-warning для SOL входа
- Применение: если ETH уже в позиции → SOL entry threshold ниже (или автоматически открывать)
- Риск: корреляция ситуативна, нужна статистика — сколько раз SOL следует за ETH
- **Когда:** 100+ labeled сигналов по обоим инструментам — проверить корреляцию

### ⏳ Constrained Strategy Optimizer (Optuna)
- Optuna перебирает параметры → backtest → устойчивые комбинации
- Только constrained: цель PF при DD ≤ Y, SL серия ≤ Z
- Requires: machine-readable JSON output из backtest (уже есть backtest_results_latest.json)
- **Когда:** после signal journal + microstructure edge

### ⏳ VIX + CME BTC COT как макро-фильтры
- VIX > 30 = risk-off, крипта падает → veto на LONG
- CME COT: позиции крупных спекулянтов → разворот
- **Когда:** 100+ labeled → проверить корреляцию macro с исходами

### ⏳ Дивергенция RSI как фильтр
- RSI-дивергенция на 1H/4H → блокировать SWING/TRENDING
- calc_rsi уже есть в indicators.py
- **Когда:** 100+ labeled → проверить корреляцию дивергенции с SL

### ⏳ Volume Profile — зоны справедливой стоимости
- VAH/VAL/POC: зоны с малым объёмом → цена проходит быстро
- TP2 уточнение через POC
- Данные: tape_recorder.py пишет с ~11 апреля
- **Когда:** достаточно tape данных

### ⏳ Cluster Search — зоны поглощения через объём + дельта
- delta = sum(buy_size) - sum(sell_size) за 1m/5m бар
- Применение: подтверждение входа
- **Когда:** 30+ дней tape данных

### ⏳ BTC.D × BTC регим-фильтр (матрица альт-сезона) — из инфополя 24.05 (канал Paranoia)
- Матрица: BTC.D↓+BTC↑ = ALTS↑↑ (альт-сезон, лонг-байас); BTC.D↑+BTC↓ = ALTS↓↓ (дамп, не фейдить вверх);
  BTC.D↑+BTC↑ = ALTS↓; остальное — нейтрально. Контекст-фильтр НАПРАВЛЕНИЯ для альт-сделок.
- **Зачем нам:** не для exit (наш текущий bottleneck), а как **фильтр отбора/байаса** — срезать сделки против
  альт-режима (особенно bb_fade: не фейдить вверх в BTC.D↑+BTC↓). Кластер макро-контекста (ср. VIX+COT, Phase G Market-Context агент).
- **Данные:** BTC.D нет в OKX/тейпе → внешний источник (CoinGecko global market cap %). Новая зависимость.
- **Как тестить:** разметить каждую нашу сделку режимом (BTC.D/BTC ↑↓→ за окно) → проверить, лифтит ли WR/expectancy
  на 91 main + 16 bb. Только если лифтит — в paper.
- **Когда:** ПОСЛЕ exit/risk модели (V3 свип). Сейчас НЕ трогаем — направление у нас уже ок, течёт на выходе.

---

## P2 — Execution / Client Layer

### ⏳ Диалог с клиентом вокруг сигнала (Q&A режим)
- После сигнала клиент задаёт вопросы — LLM отвечает в контексте snapshot
- Нужно: хранить контекст последнего анализа per user
- **Когда:** первые реальные клиенты с активностью

### ⏳ Market Digest — суточный дайджест в Telegram-канал
- Автопост в 21:00 UTC: рынок сегодня / бот сегодня / завтра
- **Когда:** после закрытия S2.3

### ⏳ Premium анализ по скрину (/deep команда)
- Вариант Б: Gemma/Qwen text-only, расширенный промпт, три сценария
- Новая кнопка или /deep команда
- **Когда:** после накопления первых клиентов

### ⏳ Client Execution Reconciliation
- Клиент присылает CSV из OKX → importer матчит к signal_id
- **Когда:** первые платящие клиенты с реальной историей

---

## P3 — Infrastructure

### ⏳ BB FADE на старших таймфреймах (1H/4H)
- BB FADE 5m убыточен на XRP/SOL; 1H бар — более весомое событие
- Бэктест: BB(20, 2.0) на 1H → mean reversion к mid за 4-8 баров
- **Когда:** после закрытия S2.3 (30+ labeled)

### ⏳ Reentry Guard (Anti-Churn)
- Кулдаун: 60s та же сторона, 180s после убытка
- **Когда:** вместе с Daily Stop Limit

### ⏳ Pattern Engine — пинбары / поглощения / inside bars
- Лёгкий детектор, возвращает (bias, confidence, strength)
- TA-Lib содержит 60+ паттернов свечей — подключается одной функцией
- **Когда:** как дополнительный фильтр входа

### ⏳ Pattern Recommendation Engine (отдельный инструмент)
- Отдельный движок (не внутри бота): пара + таймфрейм → TA-Lib scan → "обнаружен молот на поддержке, LONG, уровни X/Y"
- Детерминированный (не LLM), дополняет Concierge Analyzer
- **Когда:** после Pattern Engine фильтра, есть смысл если появятся клиенты

### ⏳ Pump Engine Phase C — Trend Accumulator Detector
- Текущий детектор: одна 1m свеча с price_pct ≥ 2% + vol_mult ≥ 2× → ловит только flash pump
- Проблема (05.05.2026): OL +23%, PENGU +12%, LAB +60% — трендовые пампы, не flash
- Добавить скользящее окно: 3-5 свечей подряд в одну сторону, суммарно ≥ 1.5-2% → трендовый памп
- Структура: импульс → зафиксировать уровень → ждать retest → отбой (пинбар) → вход
- SL под тень ретеста, TP продолжение импульса
- **Когда:** Phase C (после 50+ paper сделок с flash detector)

### ⏳ Pump Engine Phase C — Chain Re-entry + Breakeven Trail
- После входа: как только +0.5% → SL в безубыток (риск = только комиссия 0.1%)
- Закрылся по TP → тут же переоткрыться если тренд продолжается
- Каждый новый вход: SL выше предыдущего (trailing вверх)
- Работает и на retest: поймал "нож" → цена вернулась → SL в безубыток = нулевой риск
- Рождено из опыта ручного трейдинга на LAB/UB (02-04.05.2026) — слив = нет SL в безубыток
- **Когда:** Phase C, после trend accumulator detector

### ⏳ Graphify — оптимизация токенов с Claude Code
- github.com/nateraw/graphify — карта функций/зависимостей
- **Когда:** при ощутимом росте кодовой базы

---

## 🖥️ Phase S3.1 — Desktop Trading Journal App (ближайший продукт)

> **Контекст:** обсуждено 17.05.2026. Клиенты спрашивают журнал — превращаем в отдельный продукт.
> **Стратегия:** desktop app как маркетинговый ход (безопасность как USP) + бонус подписки.

### Аргументы пользователя за приложение
1. **Серьёзность подхода** — выглядит профессиональным продуктом, не "просто бот"
2. **Безопасность как USP** — API ключ только у клиента в крипте это сильный аргумент в эру скама. "Ваш ключ не покидает ваш компьютер" продаётся лучше любого "trust me"
3. **Маркетинговый ход** — криптаны идут на топовый продукт; обновления через Telegram = низкая стоимость дистрибуции
4. **Поддержка 1-2 клиентов** — легко обслужить лично, на месте или дистанционно
5. **Будущее с LLM-агентами** — обложить ошибками всё, агент анализирует журнал и выдаёт обновления → автоматизированный suport штат

### Тех. стек (решение 17.05.2026)
**PySide6 + PyQtGraph** — Python (переиспользуем 80% кода бота), Native Windows look, .exe ~80MB, LGPL.
Альтернативы (отклонены): Tauri (новый стек, нет Rust в проекте), Electron (150MB, медленный).

### Архитектура приложения
```
trading-journal-app/        ← ОТДЕЛЬНЫЙ РЕПО (своя dev цепочка)
├── app/
│   ├── main.py
│   ├── ui/
│   │   ├── main_window.py        # главное окно
│   │   ├── kpi_cards.py          # 4 KPI карточки сверху
│   │   ├── equity_chart.py       # pyqtgraph equity curve
│   │   ├── trades_table.py       # таблица сделок с фильтрами
│   │   ├── settings_dialog.py    # API ключ
│   │   └── theme.py              # тёмная/светлая
│   ├── core/
│   │   ├── okx_client.py         # переиспользуем src/exchange/okx_client.py
│   │   ├── metrics.py            # переиспользуем build_journal логику
│   │   ├── data_store.py         # SQLite локальный кэш сделок
│   │   └── credentials.py        # Windows DPAPI для API ключа
│   ├── updates/
│   │   └── telegram_check.py     # проверка обновлений через TG канал
│   └── resources/
└── installer/build.iss           # Inno Setup
```

### Безопасность (продаваемая фишка)
- API ключ только OKX READ permission (проверка прав на старте)
- Шифрование через Windows DPAPI (расшифровка только под текущим Windows user)
- Прямое HTTPS соединение только к `api.okx.com:443`
- Ноль внешних серверов с нашей стороны — клиентский ключ нигде не светится у нас

### MVP Scope (4 фазы по неделе)

**Фаза 1 (1 неделя) — Core:**
- Окно настроек: ввод API ключа + проверка READ permission
- Главное окно: таблица сделок через positions-history
- SQLite локальный кэш
- Кнопка "Обновить"

**Фаза 2 (1 неделя) — Metrics & Visual:**
- 4 KPI карточки: Net P&L / WR % / Avg R / Profit Factor
- Equity curve график (pyqtgraph интерактивный)
- Outcome pie (TP/SL/TIME)
- Фильтры: дата / пара / тип

**Фаза 3 (1 неделя) — Polish:**
- Экспорт в Excel (наш существующий journal.xlsx формат)
- Тёмная тема (must have для криптанов)
- Группировка по дням / парам
- PyInstaller → `TradingJournal-v0.1.exe`

**Фаза 4 (3-5 дней) — Distribution:**
- Inno Setup инсталлер
- Telegram канал для auto-update check на старте
- Code signing certificate ($200/год — критично против Defender SmartScreen)

### Реалистичный таймлайн
3-4 недели работы + параллельно бот накапливает сигналы (S2.3). При плотной работе — 2 недели.

### Распределение
- Скачивание через бот: `/get_app` → ссылка на .exe
- GitHub Releases (бесплатно)
- Telegram канал обновлений

### Расходы первого года
| Что | Цена |
|-----|------|
| Code signing certificate | $200-300/год |
| Domain (optional) | $10/год |
| Hosting | $0 (GitHub + Telegram) |
| **Итого** | **~$250** |

### Монетизация
**Решение пользователя:** уточнить при возврате к обсуждению.
Варианты:
- **B (рекомендую):** бесплатно для подписчиков сигналов — увеличивает retention
- **A:** отдельная подписка $10-20/мес
- **C:** Pay-as-you-go

### Будущее (Phase 5+, BACKLOG в backlog)
- LLM-агент анализирующий журнал ("ты входишь поздно в TRENDING")
- Многоаккаунтность
- Импорт из Bybit / Binance Futures
- Auto-notifications когда WR падает ниже порога
- Cross-asset correlation

### Риски (честный чек-лист)
| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| S2.3 не закроется пока строим | средняя | Параллелить — данные копятся сами |
| Windows 10/11/Server баги | высокая | VM для тестов + бета 2-3 знакомых |
| OKX API изменения | низкая | Версия pinned, мониторинг changelog |
| Не найдём 10 платных | средняя | Free version → замер интереса |
| Defender SmartScreen блокирует .exe | высокая | Code signing certificate ($200) |

### Что нужно решить ДО старта кода
1. Монетизация (A / B / C из списка выше)
2. Биржи на MVP — только OKX или сразу Bybit/Binance?
3. Иконка / название / брендинг
4. Repo: отдельный новый или внутри существующего?

### Когда стартовать
**Не сейчас** — текущая фаза S2.3 не закрыта (68/100 labeled).
**После:** S2.3 закрыта (100+ labeled, WR≥85% на последних 30) → S3.1 кодим параллельно с тем как бот продолжает работать.
Это **первый продуктовый шаг** после доказательства что движок прибыльный.

---

## 🔬 Phase G.0 — Historical Forensics & Pattern Mining (подготовка к G.1)

> **Контекст:** обсуждено 17.05.2026 поздно вечером. Пользователь предложил пред-G.1 шаг — провести forensic-анализ архива для понимания **что работало**, **что нет** и **почему**, прежде чем строить Data Lake (G.1).
> **Why:** прежде чем тренировать LLM-агентов на данных, нужно понимать паттерны. Без forensic фазы рискуем построить мета-слой вокруг ложной картины.

### G.0.1 — Commit forensics: топ-периоды vs git история

**Цель:** найти git коммиты которые соответствовали пиковым результатам каждого канала.

**Что есть:**
- Pump archive 03-09.05: пик +87% на 08.05 (со старым pump engine v1/v2 + старыми фильтрами)
- Pump current 09-17.05: -41% (новый orchestrator с B.5.7 фильтрами)
- Main: 38 дней, FAST WR=77% — устойчивый
- BB Fade (старый FADE в scanner): 60% WR на 47 сделках за апрель-май

**Метод:**
- Для каждой даты пиковой производительности → найти соответствующий commit
- Reconstruct config snapshot (config.yaml на ту дату)
- Зафиксировать "winning parameters" в `docs/historical_winning_params.md`
- Особо: pump 06-08.05 (старая логика) — какие vol_mult / price_pct / cooldown были

**Объём:** 1-2 дня research

### G.0.2 — TP pattern mining для Main

**Цель:** найти паттерны прибыльных сетапов.

**Подход:**
```
для каждого TP-сигнала в архиве + текущих:
  собираем bucket_key: (style, regime, hour_utc, adx_4h_bucket, vol_ratio_bucket,
                        side, btc_state, day_of_week)
для каждого ключа:
  posterior = TPs / (TPs + SLs)
выводим: топ-30 ключей с n>=5 и posterior>=75%
```

**Результат:** "если такая комбинация — почти гарантированный TP". Это **готовые фильтры** для Main screener.

**Объём:** 1 неделя research + 1-2 дня внедрение в код.

### G.0.3 — SL pattern mining для Main

**Цель:** найти паттерны убыточных сетапов.

**Тот же подход, противоположная сторона:**
```
выводим: топ-30 ключей с n>=5 и WR<=40%
```

**Гипотеза автора:** часть SL — из-за **недоделанных** фильтров (мы слишком мягко проверяем условие), часть — **перемудрённых** (фильтр режет хорошие сигналы по wrong reasons).

**Что искать:**
- SL кластеры по часам — может Asia session
- SL кластеры по парам — может специфичные альты
- SL после консекутивных TP — overconfidence pattern
- SL в первые 30 мин после открытия рынка / news / funding rate changes

**Объём:** 1 неделя research + iteration на фильтрах.

### G.0.4 — Training DB schema для будущей LLM (G.4)

**Цель:** **уже сейчас** начать собирать данные в формат который потом feed'нется в LLM fine-tune.

**Что должно быть в каждой записи:**

```python
@dataclass
class TrainingRecord:
    signal_id: str
    ts_utc: datetime
    symbol: str
    channel: str          # main / bb_fade / pump
    side: str
    style: str            # FAST / SWING / FADE / null

    # Полный indicator snapshot (15+ полей)
    adx_1h: float
    adx_4h: float
    ema20_1h: float
    ema50_1h: float
    bb_width_1h: float
    rsi_15m: float
    atr_15m: float
    slope_15m: float
    fvg_present: bool
    vol_ratio_15m: float

    # Market context (cross-asset)
    btc_state: dict        # {"regime": "TRENDING", "slope_1h": 25}
    eth_state: dict
    sol_state: dict

    # Tape window (когда уже есть)
    tape_5min_buy_ratio: float | None
    tape_5min_volume_usd: float | None
    tape_pre_cvd: float | None

    # Decision context
    entry: float
    sl: float
    tp1: float
    tp2: float | None
    expected_r: float

    # Outcome (заполняется лейблером после факта)
    outcome: str           # TP1 / TP2 / SL / TIME
    actual_r: float
    hold_min: int
    mfe_r: float
    mae_r: float
```

**Хранение:**
- Формат: `data/training/{YYYY-MM-DD}.parquet`
- Backfill из существующих jsonl + tape archives
- Pandas DataFrame в одной команде: `python scripts/data/load_training.py --from 2026-04-09`

**Backfill data sources:**
- logs_archive/signals/signal_log_2026-05.jsonl + signal_labels_2026-05.jsonl
- logs_archive/09.05.2026/* (signals, labels, pump)
- logs/signals/main_signals.jsonl + main_signals_labels.jsonl
- logs/bb_fade/bb_fade_signals.jsonl
- logs/pump/pump_signals.jsonl + pump_labels.jsonl
- E:\trading-data\ticks (tape для tape_5min_* fields)

**Объём:** 1-2 недели — это инфраструктурная работа, основа для G.1-G.4.

### Когда стартуем G.0

**Не сейчас.** Текущая фаза — S2.3 (100+ labeled, осталось ~25 свежих).
**После закрытия S2.3:**
- G.0.1 — commit forensics
- G.0.2 + G.0.3 — pattern mining параллельно
- G.0.4 — Training DB schema (база для всего)

**G.0 — это пред-фаза для G.1.** Без неё G.1 будет "стройка вокруг неизвестно чего".

### Записи в memory для следующих сессий
- Не предлагать кодить G.0 / G.1 / G.3 пока S2.3 не закрыта
- Завтра — продолжение обсуждения этого с пользователем
- При следующем разговоре про "патерны" — продолжаем отсюда

---

## 🎯 Phase G — Multi-Agent LLM Architecture (большой горизонт)

> **Контекст:** обсуждено 17.05.2026 — стратегическая цель проекта после закрытия текущих фаз.
> **Концепция (пользователь):** перейти от rule-based скринера к системе LLM-агентов, обучаемых на собственных данных. Сейчас main screener — это "фотография рынка на 15m close", без памяти, без распознавания паттернов, без сравнения с историей. Phase G меняет это.

### ⏳ G.1 — Data Lake: единая схема и хранение
**Цель:** все события (signal/entry/outcome/tape) → единый parquet формат пригодный для обучения.

**Что есть сейчас (фрагментированно):**
- main_signals.jsonl (74) + main_signals_labels.jsonl (68)
- pump_signals.jsonl (487) + pump_labels.jsonl (464) — с MFE/MAE/R-кратными
- smart_pump_candidates.jsonl (138) — shadow mode
- bb_fade_signals.jsonl (1)
- signal_snapshot.jsonl (50) — full context
- E:\trading-data\ticks — ~50M тиков

**Что нужно построить:**
- `src/data/training_writer.py` — единая `dataclass TrainingRecord`:
  - `signal_id, ts, symbol, channel, side`
  - `context: {indicators, regime, mtf_alignment, btc_state, eth_state}`
  - `tape_window: {pre_5min, post_15min}` агрегат тейпа вокруг сигнала
  - `outcome: {TP/SL/TIME, hold_min, net_pct, mfe_r, mae_r}`
- Формат хранения: `data/training/{YYYY-MM-DD}.parquet`
- Backfill из существующих jsonl + tape archives
- Snapshot BTC/ETH/SOL **в момент любого сигнала** (market context для cross-asset)
- `scripts/data/load_training.py` → возвращает pandas DataFrame

**Объём:** ~1 неделя, без ML, чистая инфраструктура
**Когда:** после закрытия S2.3 + Phase C critical mass (200+ pump trades)

### ⏳ G.2 — Pattern Miner (бакетный анализ без ML)
**Цель:** найти устойчивые бакеты с WR >65% и фильтровать остальные.

```
для каждого record:
  bucket_key = f"{channel}|{regime}|{adx_bucket}|{vol_bucket}|{btc_state}|{hour_utc}"
  bucket_stats[bucket_key].update(outcome)

вывод: топ-50 бакетов с n>=20 и WR>=65%
```

**Применение:** перед каждым сигналом lookup в bucket_stats. Если `n>=10 AND WR>=55%` → ENTRY. Иначе → SHADOW (логируем, не торгуем).

**Эффект:** меньше сделок, выше качество. Тестируется бэктестом.

**Объём:** ~1 неделя
**Когда:** после G.1, требует 500+ labeled

### ⏳ G.3 — Multi-Agent LLM Pipeline
**Цель:** реализация пользовательского видения — несколько LLM с разными ролями.

| Агент | Вход | Выход | Стек |
|-------|------|-------|------|
| **Market Context** | BTC/ETH 4H + новости (CryptoPanic) | "медвежий/бычий/боковик + ключевые уровни" | Sonnet 4.6 |
| **Setup Analyzer** | Свечи пары + индикаторы + market context от #1 | "сетап + confidence 0..1 + ключевая зона" | Sonnet 4.6 |
| **Tape Reader** | Tape ±5 мин от точки входа | "buyers/sellers control + cluster zones" | Haiku 4.5 |
| **Risk Manager** | Outputs #1-3 + текущая позиция | "размер позиции + SL/TP с обоснованием" | Sonnet 4.6 |

**Финальное решение:** консенсус 3 из 4 агентов с confidence>=0.6 → ENTRY.

**Стоимость:** ~$0.5-2 за решение на Sonnet (5x дешевле Opus, ~95% качества для классификации). При 10 решениях/день = $5-20/день.

**Где живёт:** новый процесс `scripts/ws/ws_agent_orchestrator.py`. Параллельный канал, НЕ заменяет main/pump/bb_fade — добавляется поверх как третий слой фильтрации.

**Объём:** 2-3 недели
**Когда:** после G.1 + G.2 (нужна data для промптов)

### ⏳ G.4 — Fine-tuning open-source модели на исходах
**Цель:** локальная модель учится на наших данных, заменяет API на повторяющихся задачах.

**Подход:**
- Open-source база: Qwen 72B или Llama 70B
- LoRA fine-tune на закрытых сделках: `(context) → (outcome)`
- Дешёвый inference: TogetherAI ~$1/M токенов или своё железо
- Метрика: prediction calibration (когда модель сказала confidence=0.8 → 80% сделок TP)

**Зачем не Anthropic fine-tune:** Anthropic не предоставляет fine-tune для широкой публики. OpenAI/Together поддерживают LoRA на open models.

**Объём:** 1-2 месяца (включая инфра под inference)
**Когда:** после 1000+ labeled через G.3

---

### 📝 Заметки по платформам (обсуждение 18.05.2026)

Вопрос пользователя: можно ли развернуть всё это на Yandex AI Studio (где у нас уже крутится LLM для Telegram постов).

**Краткий вердикт:** Yandex для нашей задачи не оптимален. Сохраняем выбор Anthropic API + локальная LoRA на Qwen/Llama.

**Сравнение платформ (для будущего ревью):**

| Платформа | Плюсы | Минусы | Цена (ориентир) |
|---|---|---|---|
| **Anthropic API (Sonnet 4.6)** | Лучший reasoning на числах, стабильность | Нет fine-tune для публики | ~$3/M вход (≈0.3 руб/1k) |
| **TogetherAI** | Хостинг + fine-tune Qwen/Llama, LoRA | Зависим от внешней площадки | $0.20-1.20 / 1M токенов |
| **Runpod GPU** | Полный контроль, дёшево при стабильном трафике | Сами админим | $0.30-2.00 / час GPU |
| **Локальное железо (4090/5090)** | Один раз заплатил, никаких лимитов | Капекс, шум, электричество | разово ~3000$ |
| **Yandex Cloud (YandexGPT Pro)** | РФ-инфра, удобный AI Studio | Дороже Claude в ~4× за токен, слабее в numerical reasoning, fine-tune ограничен форматом instruction tuning | ~1.20 руб / 1k токенов |
| **Yandex Foundation Models (Llama через DataSphere)** | Можно fine-tune через LoRA, инфра РФ | Сложнее настройка, GPU часы дороже Runpod | по часам GPU |

**Почему Yandex не подходит для G.3/G.4:**
1. Наша задача — анализ свечей, tape, классификация сетапов. Это **numerical / structured reasoning**, не русский текст. YandexGPT здесь слабее GPT-4 / Claude / даже Qwen 32B.
2. Цена за токен в ~4 раза выше Claude Sonnet при худшем качестве на нашей доменной задаче → плохой trade-off.
3. Fine-tune YandexGPT Pro есть, но через ограниченный формат — гибче брать Llama/Qwen через TogetherAI или Runpod.
4. РФ-compliance нам пока не нужен — мы не храним персональные данные клиентов на этапе личного бота.

**Когда Yandex имеет смысл (в будущем):**
- Если запускаем публичный сервис в РФ и нужна локальная инфра по 152-ФЗ
- Для отдельного агента "генерация русскоязычных постов/уведомлений в Telegram" (там Yandex и так уже работает — оставить как есть)
- Для RAG над русскими документами (например, налоговая отчётность инвесторов)

**Решение по архитектуре G.3/G.4 (фиксируем):**
- **G.3 prod-агенты** — Anthropic API (Sonnet 4.6, дешевле Opus в 5×, 95% качества для классификации)
- **G.4 fine-tune** — Qwen 32B или Llama 70B через **TogetherAI** (LoRA, $1/M токенов inference) или Runpod (если объём вырастет)
- **Telegram-генерация постов** — оставить на YandexGPT (уже работает, отдельный изолированный сервис)

**Открытый вопрос (отложен):** при каком объёме сделок (1k/10k/100k в день) переход с API на собственное железо окупается. Посчитать после G.3 когда будет реальная нагрузка.

### Критерий для перехода в Phase G
- S2.3 закрыта (100+ main labeled, WR>=80% на последних 30)
- Phase C закрыта (200+ pump, WR>=60%, PF>=2.0)
- F.1 закрыта (50+ BB Fade live, WR>=65%)
- **Только тогда** имеет смысл строить мета-слой. Без работающих моделей на старте — построим эпициклы вокруг ложной картины.

---

## 💬 Telegram Dev-Bridge — удалённый пульт к Claude + GPT (идея 18.05.2026)

**Концепция:** отдельный Telegram чат (только пользователь + бот) как UI для удалённой работы с двумя AI агентами проекта (Claude Code + GPT/Codex), запущенными локально на ПК. Никаких API расходов — используется существующая подписка Claude Code + ChatGPT.

**Архитектура:**

```
[Telegram чат: ты + бот]
        ↓ inline-кнопки выбора получателя:
   [→ Claude]  [→ GPT]  [→ Оба]  [Статус]  [Логи]
        ↓
[Локальный router-скрипт на ПК]
        ↓ subprocess вызов
   ├── claude -p "<text>"  (headless mode, читает CLAUDE.md + memory/)
   └── codex/gpt -p "<text>" (headless, читает те же файлы)
        ↓
[Ответ → Telegram → архив logs/dev_chat/YYYY-MM-DD.md]
```

**Ключевая идея:** мозги остаются локально, Telegram = транспорт. Claude и GPT синхронизированы через файлы проекта (`CLAUDE.md`, `BACKLOG.md`, `memory/`, `docs/`), поэтому им не нужно "разговаривать" через чат — они видят обновления друг друга при следующем вызове. Чат — пульт пользователя, а не переговорка ИИ.

**Зачем:**
- Удалённая работа с телефона ("как там pump?", "статус GPT блоков?")
- Передача задач на ночь без открытого VS Code
- Общая лента активности с ротацией в архив
- Видишь обе стороны (Claude + GPT) в одном месте
- Inline кнопки `/status`, `/logs`, `/gpt_progress` для быстрых проверок

**Стоимость:** ноль API токенов. Работает на твоих существующих подписках Claude Code Max + ChatGPT Plus. Лимит — rate limit подписок (не упрёмся при ~10-30 запросах в день).

**Ограничения (обязательно знать перед стартом):**
1. **Latency:** 30 сек — 3 минуты на сообщение (headless cold start + работа)
2. **Headless less interactive:** один промпт → один ответ. Для уточнений — следующий round trip
3. **Lock на файлах:** если параллельно работаешь в VS Code и через Telegram — нужен lock-файл чтобы не было конфликта на одних и тех же файлах
4. **Длинные ответы:** Telegram лимит 4096 символов. Длинные диффы/отчёты — saved в файл + краткое summary в чат с ссылкой
5. **Контекст сессии:** скрипт хранит `current_session.md` со свёрткой последних 10-20 сообщений, передаёт в каждый headless вызов как prefix

**Компоненты MVP:**
- [ ] Новый Telegram бот (отдельный от pump)
- [ ] `scripts/dev_bridge.py` — router (poll Telegram, route по кнопкам, subprocess вызов)
- [ ] Inline keyboard с быстрыми командами
- [ ] `logs/dev_chat/YYYY-MM-DD.md` — архивация с ротацией
- [ ] `current_session.md` — running context
- [ ] Lock-файл для предотвращения параллельных Claude инстансов
- [ ] start.bat / stop.bat для bridge

**Опциональные расширения:**
- `docs/dev_chat_scratch.md` — shared scratchpad для AI заметок во время длинных задач
- Команда `/handoff <task>` — формализованная передача задачи между Claude и GPT (создаёт structured запрос в docs/)
- Quick команды: `[Статус]` (summary из journal + последних логов), `[GPT progress]` (новые docs/gpt_*.md), `[Restart bot]`

**Объём:** вечер работы для MVP (1-2 часа на router, 30 мин на архивацию, 1 час на тест)

**Когда делать:**
- ❌ Не сейчас — Phase G.0 не закрыта, GPT добивает Block 3, не распыляться
- ✅ После закрытия текущего research отчёта + применения рекомендаций
- ✅ Может быть совмещено с Phase S3 (desktop app) если придумаем как — но скорее отдельный мелкий side-проект

**Где не подходит:**
- Глубокая разработка / итеративная отладка — VS Code быстрее
- Реактивная работа в реальном времени — слишком медленный round trip

**Где сильно:**
- Мониторинг и статус-проверки
- Передача задач на длинные прогоны (бэктесты, аудиты)
- Удалённая работа когда нет доступа к ПК

---

## P4 — Монетизация / S3

### ⏳ Сайт (Вариант А)
- Yandex Cloud VM, читает сигналы которые бот посчитал
- Блокер: нет универсального кода для произвольных пар
- **Когда:** 100+ labeled + стабильный движок

### ⏳ Мультипользовательская система (Copy-trading)
- Клиент подключает OKX → бот торгует по сигналам на его счёте
- **Когда:** S2, 2-3 недели реальной статистики

### ⏳ Funding Rate Арбитраж (дельта-нейтральный)
- Funding > 0.1% → шорт фьючерс + лонг спот → собираем funding
- **Когда:** после AUTO_TRADE

### ⏳ ML Signal Engine (ensemble)
- RandomForest + GradientBoosting на исторических результатах
- **Когда:** 500+ labeled сигналов

---

## Архив — Уже реализовано ✅

- ✅ DRIFT режим (core стратегия)
- ✅ Бэктестер на исторических свечах (backtest_simulate.py)
- ✅ Авто-сканер рынка (telegram_bot.py, _scanner_loop — код есть, не запускается)
- ✅ Telegram уведомления
- ✅ Автоматическое исполнение ордеров (auto_execute.py — выключено, нет средств)
- ✅ Signal log pipeline (signal_log.jsonl + label_outcomes.py)
- ✅ Батники Windows (start/stop/clear/logs/update_journal)
- ✅ OVERALL report в бэктесте
- ✅ candle_vol_delta фильтр (soft delta veto)
- ✅ V6C ranging_recovery (коммит c2262fd)
- ✅ BB FADE: not_thrust + slope_fading (коммит 088cd7b)
- ✅ FAST hold 150m→90m (коммит 73278a4)
- ✅ D2+B3 DRIFT фильтры (коммит 91c1807)
- ✅ Premium Screenshot Analysis — Gemma 3 27B, 5 категорий
- ✅ Вкладка "Реальные сделки" в journal.xlsx (build_journal.py)
- ✅ label_outcomes.py — авто-лейблинг через OKX fills-history
- ✅ analyze_signal_log.py — написан (ждёт 100+ labeled)
- ✅ WebSocket инфраструктура (ws_pump_engine.py, ws_scanner.py)
- ✅ bt_sweep_drift.py + bt_param_sweep.py — sweep harness
- ✅ Библиотека промптов (три сценария в WAIT/NO_TRADE) — коммит 11125c5
- ✅ Persistent keyboard "🔍 Анализ" в Telegram
- ✅ bt_entry_filters.py — sweep 14 фильтров × 5 hold, TP1→BE→TP2 структура (08.05)
- ✅ TRENDING FAST FVG фильтр + hold_trending_fast_minutes=120 (08.05)
- ✅ WSFeed candle4H + per-bar буферы (09.05)
- ✅ ws_main_screener.py — shadow-mode WS скринер, 29 пар (09.05)
- ✅ ws_bb_fade.py — MTF BB Fade WS процесс, 15m+5m, WR=70.6%, PF=1.89 (15.05)
- ✅ bb_fade_label_outcomes.py — лейблер BB Fade сигналов (15.05)
- ✅ tape_recorder.py в start_all.bat — автозапуск tape recorder (15.05)
- ✅ Pump notifications в Telegram community group (13.05)

---

## Архив — Протестировано и отклонено 🧪

### 🧪 Independent ATR TP/SL from entry price (2026-04-07)
- independent entry ± ATR: WR 81%→57%, PF 2.64→1.53, DD 17.7%→28.7%
- TIME_EXIT снизился, но SL вырос — не улучшение

### 🧪 BB + Volume 1m/5m скальп (2026-04-08)
- Пользователь вручную 13/13 прибыльных за сессию — но бэктест на 5m BB: WR 65.5%, PF 4.79
- BB FADE внедрён как Канал 2, работает на BTC/ETH/DOGE

### 🧪 DRIFT entry phase detection (2026-04-04) — 3 гипотезы:
- TP1 geometry veto: DRIFT 695→54 сигналов (-92%) — уничтожает канал
- BB compression after impulse: не срабатывает ни разу за 56 дней
- Trigger extension veto: WR 66%→60%, баланс +181%→+103% — режет лучшие сигналы
- Вывод: проблема в качестве режима DRIFT, не в точке входа

### 🧪 Cadence и freshness (2026-04-05):
- ATR_1H вместо ATR_15m для SL: WR 72%→74%, баланс +181%→+135% — нет
- Hold FAST 480m/SWING 720m: WR 72%→63%, PF 1.78→1.20 — нет
- 5m polling cadence: WR/PF идентичны, mfe +27% — без фильтра дублей бесполезно
- 1m freshness filter: TP и TIME_EXIT неотличимы по 1m структуре

### 🧪 DRIFT ADX min 12→8 (2026-04-05):
- PF 1.78→1.63, баланс +181%→+125% — зона ADX 8-12 это шум

### 🧪 Walking the band для BB FADE (2026-04-15):
- n=26, WR=46.2%, PF=1.05 — нестабильно по периодам; нужно 90+ дней

### 🧪 BB HTF фильтр (2026-04-12):
- Требует hold 480-720m + частичный выход — симулятор не поддерживает
- Отложено до правильной инфраструктуры

### 🧪 DRIFT baseline без D2+B3 (2026-04-25):
- n=158, WR=74%, sim=+87.7% — стало базой для D2+B3 сравнения

### 🧪 V-bottom гипотезы V1-V5C (2026-04-19):
- Все хуже baseline — DRIFT SHORT veto уничтожает эффект
- V6C ranging_recovery решил проблему через RANGING режим

---

## ❌ Закрытые идеи

### ❌ Claude Code slash-команды (2026-04-25)
- Поведение зашито в CLAUDE.md и SESSION.md — slash-команды избыточны

### ❌ Памп-сканер на MEXC фьючерсах
- Требует отдельный репо, интеграцию MEXC WS
- OKX pump engine уже решает ту же задачу на знакомой бирже
- Закрыто: дублирует pump engine, без дополнительной ценности пока OKX pump не изучен

---

## Отдельные проекты (не расширение текущего бота)

### Арбитражное направление
- Статистический арбитраж BTC→ETH лаг — нужен WS + tape данные
- Арбитраж альткоинов между биржами — требует 2 биржи
- Funding Rate арбитраж — требует AUTO_TRADE
- Новостной арбитраж — NLP + быстрое исполнение
- Polymarket информационный арбитраж — следить за запуском Polymarket Futures

### Smart Money / Microstructure
- Order Block + FVG — после накопления сигналов для бэктеста
- On-Chain Whale Tracking — Whale Alert API
- Складной метр (вложенные трендовые линии) — нужна база swing-уровней
- RSI Heatmap скринер — при расширении пар за текущие 5
- **Маркетмейкинг — Avellaneda–Stoikov (2008)** — фундамент MM: reservation price от инвентаря (`r = s − q·γ·σ²·(T−t)`), HJB → замкнутые формулы спреда. Источник: reel `docs/инста трасткбрикция/тема9` (24.05.26). ⛔ НЕ для текущего бота — это maker-бизнес, не taker: наша комса 0.20% мгновенно убивает спред-захват; нужны мейкер-ребейты, низкая латентность, капитал, борьба с adverse selection. Отдельное направление, ближе к «стабильному доходу», чем моментум — вернуться ТОЛЬКО если осознанно свернём в MM. Старт: статья A-S + автор обещал код на GitHub за «!» в комментах.

### Мониторинг и UX
- **Pump Telegram уведомления (13.05.2026)** — OPEN/CLOSE события из ws_pump_orchestrator в Telegram. Формат как в логе: символ, направление, entry/SL/TP при открытии; исход+PnL+hold при закрытии. Реализация ~15 строк, не влияет на стратегию. Вернуться после WR>55%.

### Advanced Trading
- Связка опционы + фьючерсы — S3+, требует OKX Options API
- Order Book скальпер — отдельный репо, другая архитектура
- Flash Crash Bounce детектор — отдельный модуль
- Fear & Greed + новостной слой — после закрытия S1
- FADE / Mean Reversion режим — отдельный бэктест и SL/TP
