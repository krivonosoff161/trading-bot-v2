# TASK для Codex — 🅱 встроить 1-2 keyless источника в Scout #1 (31.05.2026)

## Цель
Из своего shortlist живьё-проверить топ-кандидаты и **встроить 1-2 рабочих keyless источника** в Scout #1, по образцу существующих источников. Не «список», а рабочий код + проверка.

## Контекст (прочитай сначала)
- `ROADMAP.md` — два слоя (🅰 анализатор / 🅱 скауты-контекст). Ты делаешь 🅱.
- `docs/scout1_source_candidates_2026-05-31.md` — твой shortlist (топ-5).
- **ВАЖНО — файлы ПЕРЕЕХАЛИ** (рефактор сегодня): Scout #1 теперь `src/scout/daily_digest_collector.py`, оркестратор `src/scout/research_scout_orchestrator.py`, данные пишутся в `logs/scout/` (НЕ в старый `scripts/analysis/research/digest_data/`).

## Шаги
1. **Живьё-проверь** топ-3 (НЕ на словах — реальным запросом): **Google News RSS**, **DexScreener API**, **Polymarket Gamma API**. Для каждого: отвечает ли keyless? формат? есть ли `время + заголовок/число` (для OOS-лога)?
2. **Встрой рабочие 1-2** в `src/scout/daily_digest_collector.py`: новая функция-источник + добавить в bundle, **строго по образцу существующих** (try/except → graceful skip → честный `sources_failed`, как у RSS/CoinGecko). Не ломать существующие источники.
3. **Запусти** коллектор → подтверди, что в `logs/scout/bundle_latest.json` появились новые поля и `sources_ok` вырос.
4. Если источник на проверке оказался платным/мёртвым/нестабильным — честно НЕ встраивать, отметить.

## Готово когда
- `src/scout/daily_digest_collector.py` обновлён (1-2 новых keyless источника), запускается, bundle содержит их.
- Дописан блок в `SESSION.md`: «↪ Codex сделал: встроил <источники> в Scout #1, sources_ok=N, файл src/scout/daily_digest_collector.py» + коммит (публичный репо).

## Граница (строго)
- **READ-ONLY к рынку, keyless.** НЕ добавлять ключи/токены, НЕ трогать `.env`/`AUTO_TRADE`/`config.yaml`/прод-движок.
- Биржевые price-API (Binance/Coinbase/Kraken/...) НЕ брать (дубль цены OKX). Соцсети — позже.
- Не выдумывать: «звучит keyless» ≠ «проверено» — встраиваешь только то, что РЕАЛЬНО ответило вживую.
- Стиль/формат bundle — как в текущем коде, без переписывания каркаса.
