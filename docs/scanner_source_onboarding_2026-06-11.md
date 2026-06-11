# Сканер: онбординг источников «один на слой» — 2026-06-11

Состояние инжеста после стабилизации (контекст):
- **SEC EDGAR починен и live**: primary-doc экстракция (тела филингов вместо 21–43 символов),
  бэкфил 9/9, метаданные CIK/форма/accession/items в `machine_docs.metadata_json.sec_filing`.
- **Google-резолвер затроттлен**: пауза `SCANNER_GN_DELAY_S` (4с) между резолвами, cooldown
  `SCANNER_GN_COOLDOWN_S` (30 мин) после 429; метрики `GN.metrics()`.

## Эксперимент: ровно один кандидат-источник на слой

| Слой | Источник | Статус | Что это |
|---|---|---|---|
| L1 | `etf_flow` | ⛔ выключен, `needs_provider` | контекст-адаптер потоков спот-ETF; слот под ежедневный CSV трейдера (`data/scout/etf_flows.csv`, формат `date,ticker,asset,flow_usd_m,source`); включение: `enabled: true` в реестре. Только строка market_ctx для L1 — карточек/GO не создаёт |
| L2 | `token_unlocks` | ✅ включен, `needs_key` | Tokenomist-адаптер готов, без `TOKENOMIST_API_KEY` честно молчит. Данные не фейкуются. (DexScreener flow_metrics уже live как L2-улучшение) |
| L3 | `investing_commodities` | ✅ включен, `candidate` | прямая commodities-лента с телами статей (уход от google-обёрток). Kitco RSS мёртв (404), mining.com блокирует (403) — проверено 11.06 |
| L4 | `rigzone` | ✅ включен, `candidate` | прямой отраслевой нефтегаз-wire (upstream/LNG/санкции/инвентори) |
| L5 | `globenewswire_public` | ✅ включен, `candidate` | официальные пресс-релизы публичных компаний (IR-класс, primary). Дополняет sec_edgar (PR ≠ филинг). Большой объём режется роутером ДО LLM |

Все новые источники: `lead_class: LAGGING`/контекст → **GO напрямую невозможен**
(предохранитель «GO+side только LEADING» в коде `scanner_v0`, есть тест). Новые айтемы
идут через существующий пайплайн (ingest → buffer → router → cheap → гейт → chief),
дедуп/идемпотентность не обходятся.

## Как оценить через 24–48 часов

```
python scripts/analysis/source_onboarding_report.py
```
Отчёт (stdout + `reports/source_onboarding/<date>/summary.md`) на источник: raw_items,
machine_docs, full_body/title_only, avg_text_len, карточки/chief/телега/NO_GO, зрелые
исходы/idio, стоимость, рекомендация (keep / observe / disable / needs key / needs parser).

Пороги чеклиста: raw ≥5/48ч · full_body ≥50% (direct) · title_only ≤40% ·
chief-вызовы впустую <15 · ≥1 осмысленная карточка.

## Как быстро выключить плохой источник (роллбэк)

Одна строка в `src/scout/config/source_registry.yaml`: `enabled: false` у источника —
следующий проход сканера его не читает (процесс спавнится заново каждый проход,
рестарт не нужен). Проверка:
`python -c "from src.scout.router import enabled_sources; print(list(enabled_sources()))"`.

## Границы

Ордер-движок / `.env` / `AUTO_TRADE` / Telegram-направление не тронуты. Эксперимент
обратим целиком: 3 строки `enabled: false` возвращают состав источников к 10.06.
