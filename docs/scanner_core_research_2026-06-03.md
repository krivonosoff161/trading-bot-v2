# Scanner CORE research — слой «вывода» (рой 17 агентов, 03.06.2026)

> Дип-ресёрч ядра инфо-эдж сканера. 17 агентов (8 осей × research+verify + синтез), 1.9М токенов, GitHub-поиск + скептик-верификация. Параллельно Codex по `TASK.md` — сверить.

## ЦЕНТРАЛЬНОЕ ПЕРЕОСМЫСЛЕНИЕ
**Граница: ДЁШЕВО решает «ЧТО ЭТО» (актив/слой/тип/материальность/дубль/в-цене), LLM решает «СТОИТ ЛИ» (сюрприз vs консенсус, асимметрия, инвалидация, GO/NO_GO).**
V0 смешивает оба в один дорогой LLM-вызов сразу после regex-матча тикера → корень и слива бюджета, и слепоты к слоям.

## PIPELINE ВЫВОДА (каскад S0-S9)
- **S0 INGEST** → единый `raw_event` {source, lead_class(LEADING/COINCIDENT/LAGGING), ts_source(UTC-норм!), ts_ingest, headline, lead, url, asset_hint}. lead_class = поле данных (карта источник→класс), НЕ инструкция в промпт.
- **S1 РОУТЕР актив/слой** (regex, 0 токенов, на заголовке): чинить first-match-wins → собрать ВСЕ хиты + tiebreak (cashtag/тикер в первых N словах: «Solana про Bitcoin ETF»→BTC) + short-ticker veto. Нет актива → DROP в drops.jsonl.
- **S2 ТЕМПОРАЛ будет/произошло** (spaCy token.tag_ VBD/VBN + dateparser + config-словари, ~60-70% без LLM). Headlinese-фикс: present-simple результативный глагол БЕЗ future-маркера = REALIZED («Bitcoin crashes 6%»=уже в цене). Сомнение→AMBIGUOUS→LLM.
- **S3 МАТЕРИАЛЬНОСТЬ** (frozenset по токенам, 0 токенов): negative-genre стоп-лист (price analysis/prediction/how to/sponsored) → drop; positive per-layer термин (hack/listing/unlock/SEC/OPEC/earnings). Все дропы → drops.jsonl (анти-survivorship).
- **S4 ДЕДУП СОБЫТИЯ** (N лент→1, ПЕРЕД LLM = главный рычаг): canonical_url (drop utm/query) → RapidFuzz token_set≥90 → Model2Vec cosine≥0.82 (RU↔EN). Дубль → confirm_count++ (НЕ новый LLM; source_count = бесплатный сигнал материальности). TTL-окно 48ч.
- **S5 page_extract** ТОЛЬКО для выживших (сейчас зовётся ДО фильтра = лишняя сеть/CPU/риск-429).
- **S6 ОКНО/СЮРПРИЗ** (арифметика, 0 токенов): REALIZED матчит pending → **z=(actual−consensus)/σ** (формула Citi/Scotti). |z|<0.5→авто-NO_GO БЕЗ LLM; |z|>1 + dir-match + малый pre_drift→GO-кандидат. pre_drift (сколько отыграно ДО) из Polymarket/OKX = «в цене». Консенсус keyless: NASDAQ epsForecast/EIA/Cleveland/ForexFactory/Polymarket.
- **S7 СЖАТИЕ** (sumy extractive, CPU): тело→выжимка 800-1200 симв, числа выживают. Вход-токены 3-4x вниз.
- **S8 LLM Qwen3** (~20-60 кандидатов/день): роль МЕНЯЕТСЯ — не «угадай консенсус из памяти» (галлюцинация=грабли Main), а «вот посчитанный z=1.8, pre_drift=2%, confirm=6, LEADING — интерпретируй в сценарий». scout_analyst НЕ менять.
- **S9 ПРЕДОХРАНИТЕЛЬ+ЖУРНАЛ**: GO+side≠none в коде ТОЛЬКО если ≥1 LEADING (страховка от миража). + llm_budget.jsonl + cost-cap (max N/час→деградация без облака).

## ЧТО МЫ УПУСТИЛИ (главное, код-подтверждено)
1. **Дедуп по URL ≠ по событию** (#1). card_id=sha1(url) → 1 листинг из 5 лент = 5 вызовов+5 карточек+кривой WR. Самый дешёвый и важный фикс.
2. **Pending ФИЗИЧЕСКИ МЁРТВ** (пишет '') → тезис «эдж=зазор будет/произошло» НЕ операционализирован → surprise в промпте = галлюцинация = ровно грабли Main «известный катализатор=мираж».
3. **Сюрприз = АРИФМЕТИКА не LLM**: z=(факт−консенсус)/σ, keyless-консенсуса больше чем думали. |z|<0.5→авто-NO_GO без токена. Главный анлок.
4. **Pre-drift** (сколько отыграно ДО) — ключевой измеритель «в цене», в V0 нет даже в плане.
5. **Baseline хардкод BTC** ломает excess для слоёв 3-5 (NVDA/золото/нефть vs BTC = бессмыслица) → per-layer SPY/QQQ/Brent/DXY.
6. **Headlinese-слепота**: «Bitcoin crashes»=прошлое, наивный tense врёт системно.
7. **OKX instruments WS preopen** — самый дешёвый честный LEADING (листинг до толпы, тот же okx_client) — был под носом.
8. **page_extract до фильтра** — лишняя работа.
9. **lead_class в КОД, не в промпт** (GO-side только если LEADING) — страховка от миража Main.
10. **Бюджет не наблюдаем + нет cost-cap** (usage уже логируется, сканер выбрасывает).
11. **Развилка сюрприза**: numeric (макро/акции, есть консенсус-число) vs attention (альты — нет числа; ожидание=соц-консенсус, дельта=дивергенция цена↑/упоминания↓). Формула на альтах НЕ работает.
12. Холодный старт σ → псевдоточность z (low_confidence до N>20). L3-металлы = беднейшая keyless-яма (вести слабее, последним).

## СТОИМОСТЬ (конкретно, анкер 0.50₽/1k подтверждён)
- V0 сейчас (1 лента, LIMIT=3): копейки/день.
- **V1 БЕЗ каскада** (флуд ~2000-5000 новостей/день): **~32k₽/мес — неподъёмно.**
- **V1 С каскадом** (2000→S1 400→S3 80→S4 50→S6 30→LLM 30 со сжатием): **~630₽/мес. Экономия ~50x.**
- Слабый ПК (GTX1050 3GB/8GB): **ни один подтверждённый тул не требует GPU** (Model2Vec numpy 40MB, spaCy-sm 13MB, RapidFuzz C++, DeBERTa ONNX-CPU).

## BUILD-VS-BUY (после скептик-верификации)
- **build:** роутер (~40 строк), материальность frozenset, окно-хранилище (битемпоральный JSONL ~50 строк), budget-лог.
- **buy:** spaCy en_core_web_sm + dateparser (темпорал), RapidFuzz + Model2Vec potion-multilingual (дедуп), Polymarket+ForexFactory+SEC EDGAR+Stooq (консенсус/резолв), OKX preopen WS + GoPlus (LEADING/VETO), sumy (сжатие), Yandex Qwen3 (мозг).
- **ЗАРУБЛЕНО:** flashtext (заморожен 2018), pyeventsourcing (overkill), unisim (архив+TF), GDELT-realtime (429), CryptoPanic free (удалён 01.04.26), локальный LLM как мозг (слаб). Telethon — с **Codeberg** (GitHub архивирован 21.02.26).

## ПОРЯДОК СБОРКИ (фазами — главный риск ОВЕРИНЖИНИРИНГ V0)
- **Этап 0 (сейчас, копейки, без зависимостей, чинит код-подтверждённые баги):** поля build_row (event_type/materiality/temporal/lead_class/baseline_symbol/router_version) + drops.jsonl + llm_budget.jsonl + cost-cap + canonical_url-дедуп + subject-эвристика match_asset.
- **Этап 1:** sumy-сжатие + перестановка page_extract + S3 frozenset.
- **Этап 2:** темпорал (spaCy+dateparser) → наполнение pending из FUTURE (без матчинга пока).
- **Этап 3:** lead_class поле+правило + OKX preopen WS.
- **Этап 4:** окно/сюрприз (expectation_poller → resolver + z + pre_drift). 3 блокера: event-дедуп, валидатор recorded_at<resolved_at, σ-low_confidence.
- **Этап 5:** дедуп-скейл (RapidFuzz→Model2Vec).
- **Этап 6:** слои 3-5 (EDGAR+Stooq, L5 первый, L3-металлы последний).
- **Этап 7 (V1+):** Telethon → Tree-of-Alpha → GoPlus VETO → new-pools → zero-shot материальность.

## ГЛАВНЫЙ РИСК
**Оверинжиниринг V0** (рефреном во ВСЕХ 8 верификациях) — втащить весь каскад/NER/3 хранилища на 3 карточки/проход = монстр-код против CLAUDE.md. Лекарство: строго по этапам, тонкий срез сейчас.

## РЕШЕНИЯ ДЛЯ ТРЕЙДЕРА (для V1, не сейчас)
1. Бюджет Яндекса ₽/сутки? (от него порог материальности + агрессивность cost-cap).
2. Free-key источники под money-guard (FRED/EIA — бесплатные, не торговые, read-only): заводить? (ALFRED-vintage = золотой anti-survivorship для макро).
3. Telegram-ингест: api_id основной акк или burner? (userbot ToS-грей).
4. L3-металлы слабейший — принять/блокер?
5. Pre-IPO перпы: EDGAR S-1 + Stooq достаточно, или ждать TG для rebase-механики?
6. Кто размечает ground-truth event_type (журнал→переобучение фильтра)?
7. Окно дедупа 48ч глобальное или per-layer?
