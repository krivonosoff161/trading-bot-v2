# Ядро «вывода» сканера — СИНТЕЗ (рой Claude × Codex, 03.06.2026)

> Две независимые глубокие версии: рой Claude (17 агентов, `docs/scanner_core_research_2026-06-03.md`) + Codex (`scripts/analysis/research/docs/scanner_inference_core_research_2026-06-03.md`). **Сошлись на ~95%** — это и есть главный результат: архитектура подтверждена двумя путями. Этот док = что строим.

## СХОДИМОСТЬ (оба, независимо → высокая уверенность)
1. **Центральное:** детерминированный ДЕШЁВЫЙ фильтр ДО LLM. LLM получает **нормализованный event-candidate + window + surprise**, НЕ сырой поток. LLM — финальный аналитик, НЕ роутер / НЕ дедуп / НЕ база.
2. **Слабые места V0** (оба, по коду): один источник; regex-роутинг ломается на коротких тикерах (SOL/AI/OP/H/OIL); layer=1 хардкод; **дедуп по URL ≠ по событию** (1 событие из N лент = N карточек+вызовов+кривой WR); нет materiality-gate; нет будет/произошло; **pending мёртв** → surprise = LLM-угадайка (грабли Main).
3. **Pipeline** (почти идентичный): ingest → **entity-роутер (актив/слой)** → **темпоральный роутер (будет/произошло/контекст)** → **materiality-gate** → **event-дедуп** → **window-state** → **surprise-delta** → LLM → журнал.
4. **Роутер:** entity-map + правила, title>body, короткий тикер требует 2-го подтверждения, дизамбигуация (SOL только с Solana/$SOL). Скоринг с порогом. Инструмент: spaCy EntityRuler / свой словарь + RapidFuzz.
5. **Темпорал = 3 класса** (не 2): будущее / реализовано / **контекст** (обзоры/мнения — не будит LLM). Источник = сильный prior (SEC=realized, календарь=expected). dateparser для дат.
6. **Материальность layer-specific** — главный бюджетный gate до LLM. Per-layer event-families + дроп шума (price recap/opinion/how-to). Скоринг с порогом.
7. **Event-дедуп:** canonical title + RapidFuzz token_set ~88-90 + datasketch/MinHash на масштабе. Дубль → **source_count++ (сигнал материальности, НЕ новый LLM-вызов)**.
8. **Window-state:** expected открывает pending → realized матчит по asset+family+окно → surprise. **Одна карточка на СМЕНУ СОСТОЯНИЯ, не на источник.** Классы сюрприза: timing / magnitude / direction / mechanics / none.
9. **Surprise нужен CONSENSUS-store** — без сохранённого ожидания сюрприз не посчитать. Оба: это упущенный класс источников.
10. **Official-first источники** (SEC EDGAR keyless / OKX announcements / EIA-OPEC) важнее новостных сайтов. **Latency-аудит** (observed−published на источник): RSS нельзя мешать с push.
11. **Нет магического GitHub-пакета** «понимающего торговые новости» — оба явно. Берём КИРПИЧИ (Trafilatura/Telethon/SEC/EIA/GoPlus/spaCy/RapidFuzz/datasketch/dateparser), **ядро пишем сами** — тонкая детерминированная rules/state-машина С ТЕСТАМИ.
12. **Воронка** (оба сошлись): 300-800 raw/день → 120-250 routed → 30-80 material → 10-30 переходов → **5-20 LLM-карточек/день.** Детерминизм режет 90-97% ДО облака.
13. **Порядок:** детерминированное ядро + тесты СНАЧАЛА, потом источники по приоритету (OKX announcements → SEC → Telethon → EIA/OPEC → GoPlus → push). Оба.

## ЧЕМ ДОПОЛНЯЮТ (взять лучшее из каждого)
**От роя:**
- **Жёсткая стоимость в цифрах:** без каскада ~32k₽/мес, с каскадом ~630₽/мес (50x). `llm_budget.jsonl` (usage уже логируется, выбрасывается) + **cost-cap rate-limiter** (max N/час → деградация без облака).
- **Surprise = чистая АРИФМЕТИКА** `z=(actual−consensus)/σ` (Citi/Scotti). **|z|<0.5 → авто-NO_GO БЕЗ токена.** + **pre_drift** (сколько отыграно ДО) = операционализация «в цене».
- **Концепт-развилка:** numeric-сюрприз (макро/акции, есть число) vs **attention-сюрприз (альты — нет числа; дельта=дивергенция цена↑/упоминания↓)**. Формула на альтах НЕ работает = два движка.
- **baseline хардкод BTC ломает excess для слоёв 3-5** → per-layer SPY/QQQ/Brent/DXY. Холодный старт σ → low_confidence до N>20.
- Headlinese-слепота («Bitcoin crashes»=прошлое); spaCy tense через token.tag_ VBD/VBN, НЕ token.morph. Model2Vec potion-multilingual для RU↔EN дедупа.

**От Codex:**
- **3 конфиг-файла (чистая концептуализация):** `entities.yaml` + `event_taxonomy.yaml` + `source_registry.yaml`.
- **Retry-inbox статусы:** `seen_raw / processed / failed_retry` — не терять заголовок при временной ошибке extraction/LLM (рой это упустил).
- **Source trust registry:** тип источника `official/primary/wire/aggregator/tg_alpha/tg_noise`.
- **Mechanics registry** (pre-IPO rebase = отдельная event-family, не «новость про компанию»).
- Инструменты для проверки: `cryptocurrency.cv` (200+ крипто-источников), `nfin` (Nasdaq earnings/IPO-календарь), `reader`(lemon24) для RSS-state.
- Outcome **event-window-based, не card-based**; метрика `missed_move` для NO_GO (если актив сделал крупный excess ПОСЛЕ NO_GO).

## ЕДИНЫЙ ПОРЯДОК СБОРКИ
**Этап 0 (СЕЙЧАС — оба согласны: дёшево, без новых зависимостей, чинит КОД-ПОДТВЕРЖДЁННЫЕ баги):**
- canonical_url-дедуп внутри seen (бьёт Google-News-реролл) + фикс роутера (multi-match + subject-эвристика: тикер в первых N словах, не first-of-list);
- поля журнала: `event_type/phase`, `materiality_score`, `temporal`, `lead_class/source_class`, **`baseline_symbol`** (per-layer), `router_version`, `drop_reason`; `schema_version=2` сразу (миграция append-only иначе дороже);
- `drops.jsonl` (анти-survivorship фильтра) + `llm_budget.jsonl` + cost-cap;
- retry-inbox статусы (seen_raw/processed/failed_retry).

**Этап 1:** 3 конфига (`entities.yaml`/`event_taxonomy.yaml`/`source_registry.yaml`) + `router.py` (entity+temporal+materiality, чистые функции С ТЕСТАМИ) + sumy-сжатие + перестановка page_extract (фильтр до extract).

**Этап 2:** event-дедуп (canonical+RapidFuzz; datasketch/Model2Vec при росте) + `event_store.py` (raw/windows/clusters).

**Этап 3:** window-state + surprise (expectation_poller → битемпоральный store + валидатор recorded_at<resolved_at → resolver z + pre_drift). Numeric-ось сначала (макро/акции), attention-ось (альты) отдельно.

**Этап 4:** источники по приоритету — OKX announcements/preopen → SEC EDGAR(L5) → Telethon → EIA/OPEC(L4) → GoPlus VETO(L2) → GDELT recall (металлы, не горячий).

## ГЛАВНЫЙ РИСК (оба били рефреном)
**ОВЕРИНЖИНИРИНГ.** Весь каскад на 3 карточки/проход = монстр-код против CLAUDE.md. Лекарство: строго по этапам, Этап 0 — тонкий срез сейчас. И помнить: **каскад делает дёшево+честно, но эдж доказывает ТОЛЬКО форвард-журнал (excess).**

## РЕШЕНИЯ ТРЕЙДЕРА (для этапов 3-4, не для Этапа 0)
1. Бюджет Яндекса ₽/сутки? (порог материальности + агрессивность cost-cap).
2. Free-key источники под money-guard (FRED/EIA — бесплатные, не торговые, read-only): заводить? (anti-survivorship для макро).
3. Telegram-ингест: api_id основной акк или burner?
4. L3-металлы слабейший keyless-слой — принять последним / блокер?
5. Pre-IPO перпы: EDGAR S-1 + Stooq достаточно, или ждать TG для rebase-механики?
