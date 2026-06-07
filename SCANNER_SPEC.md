# SCANNER — спецификация (со слов трейдера, 02.06.2026)

> Источник правды по НОВОЙ системе. Записано с диалога-проектирования. Принцип: **фильтр-машина (GO/NO-GO), не альфа-машина.** Paper-first, сбор данных, строгость учим из журнала.

## Суть
Событийный фильтр-сканер с журналом. Сидит на новостях + цене → на триггере достаёт контекст слоя → прогоняет фильтр-слои → выдаёт **GO / NO-GO + асимметрия + инвалидация** (НЕ «купи») → пишет в журнал → позже дописывает исход. Деньги — только когда форвард-журнал докажет net+.

## 5 СЛОЁВ (каждый — свои парсеры, НЕ в кучу)
1. **Крипта с историей** (BTC/ETH/ликвид) — есть и цена-история, и новости.
2. **Альты / мемы** — истории НЕТ, сигнал только в потоке.
3. **Металлы** (медь/серебро/золото) — макро/Китай/шахты/доллар.
4. **Ресурсы** (нефть и пр.) — геополитика/OPEC/EIA/танкеры.
5. **Акции** (+ pre-IPO перпы OpenAI/Anthropic/SpaceX) — earnings/SEC/релизы/механика OKX.

Каждому слою — **свой пак новостных источников + флагов.** Общий — только тонкий **макро-слой** (Fed, доллар, risk-off). В основном всё индивидуально.

## Источники / парсинг (ОТКРЫТО — нужен GitHub-ресёрч)
Решить: **как парсим + какие источники + делать ли выжимки.** Не изобретать — на GitHub есть готовое:
- **новостной поток** (event-driven: RSS, Telegram-листенер, news-webhook);
- **парсинг / страница → md/json** (Firecrawl, Jina Reader, Trafilatura);
- **сжатие/выжимка** страниц в машинный вид.

### Альты/мем — особый, самый глубокий пак (истории нет → весь сигнал в потоке)
- **теги + соцсети** (анонсы там);
- **движения кошельков** (девы / крупные холдеры / перелив по биржам — inflow/outflow);
- on-chain аномалии, концентрация инсайдеров (red-flag).
- Принцип трейдера: такие движения **по ТА = скам, НО дают заработок** → читаем поток, не свечи.

## Фильтр-слои (чего нам не хватало — добавить в разбор)
1. **«в цене?»** — не «событие X», а «X + рыночная вероятность + сколько отыграно» (Polymarket / SEC EDGAR / календари отчётов/тарифов).
2. **red-flag VETO** — инсайдер-концентрация / FDV-ликвидность / scam-варны (ZachXBT и пр.) → может перебить бычий катализатор (кейс LAB).
3. **обе стороны** — тянуть и бык, и медведь (кейс меди: пропустили Cobre Panama).
4. **механика инструмента** — pre-IPO перпы: OKX rebase/конверсия/делист-fair-value (это гэпает P&L, не нарратив).
5. **no-edge zone** — мега-капы (NVDA): всё в цене, инфо-эдж ~ноль → помечать, не сигналить.

## Кто/как анализирует
- **Оркестратор** — цикл: триггеры (цена/объём/слом + новость-флаг + календарь), нормализация по волатильности, скоринг совпадений → решает **момент + GO/NO-GO**.
- **Аналитик-мозг** — тот же LLM-разбор, что построен (llm_formatter), теперь будится оркестратором и кормится контекстом СЛОЯ.
- На **GO → график с разметкой** (`chart_renderer` есть) + **показываем ЧТО и КАК рассчитали** (не душить параметрами).
- Дёшево: пре-фильтр локально → дорогой разбор облаком ТОЛЬКО на high-score.

## Журнал (НОВЫЙ — старый под Main не годится)
Поля:
- **слой** (1-5);
- **триггер** (что разбудило);
- **решение GO / NO-GO**;
- **метрики когда GO** (уровни/асимметрия/инвалидация);
- **прогноз + СРОК** (горизонт анализа);
- **исход (+/−)** — дописывается позже → форвард-счёт.
Цель: через N строк видно, какие триггеры/вердикты по слоям реально в плюс → усиливаем; шум → выкидываем.

## Граница (держать)
- **Paper, без денег**, пока журнал не покажет net+ на форварде.
- Порог GO в paper **свободный** — логируем ВСЁ, строгость учим из данных, не навешиваем заранее.
- Вывод часто = NO-GO / «в цене» — это **фича** (спасает от LAB и погони за хаями).

## Вывод (как выглядит для трейдера)
Карточка в Telegram: `актив | триггер | катализатор | в цене? | red-flag | механика | ВЕРДИКТ GO/NO-GO + инвалидация`, на GO — с графиком. Всё → строка в журнал.

## Что на полке (переиспользуем)
scout (RSS/новости) · analyzer (LLM-разбор) · chart_renderer (график) · okx_client (цена) · telegram (доставка) · records/journal-паттерн.

---

## ВЫБРАННЫЙ СТЕК (рой GitHub, 02.06) + порядок V0

### Пики по слоям (keyless/open, под слабый ПК GTX1050/8GB)
| Слой | PICK | лиц. | нота |
|---|---|---|---|
| news push | **Tree of Alpha free WS** (`wss://news.treeofalpha.com/ws`) | сервис, keyless | sub-second push крипто-новостей, ~20 строк на `websockets` |
| + Telegram | **Telethon** (Codeberg, жив, last 23.05.26) | MIT | листенер каналов трейдера, real-time `NewMessage` |
| + RSS-хвост | **reader + feedparser** | BSD | макро/акции/металлы/нефть, стейтфул дедуп (fire-on-new) |
| page→текст | **Trafilatura** | Apache-2.0 | F1 0.958, MD/JSON + date-метаданные, CPU-only |
| сжатие | **sumy** (extractive) | Apache-2.0 | режет до фактов, **числа/таймстемпы выживают дословно**, без GPU |
| соц (alt/mem) | Telethon core + **CoinGecko** (keyless new-coin/trending) + twscrape (burner, ToS-грей) | MIT | X только через burner — не основа |
| on-chain | **GoPlus** (EVM, keyless) + **RugCheck** (SOL, keyless) | API | insider%/honeypot/holder-концентрация = **red-flag veto** |

### Минимальный V0 — первая карточка на BTC (3 новых файла + полка)
1. `src/scout/news_ws.py` — Tree of Alpha WS → нормализация в `{ts,source,headline,url,raw}`, фильтр BTC/мажоры.
2. `src/scout/page_extract.py` — Trafilatura: `url` → чистый текст + date (fallback readability-lxml).
3. Аналитик = **готовый** `llm_formatter` (Yandex Qwen3) → карточка.
4. Доставка = **готовый** `telegram.send_message_to` (@lektorTP_bot).
5. Лог = паттерн `forward_series.csv` в `logs/scout/` (идемпотентность уже решена).
(social/on-chain/sumy ждут V1 — у BTC нет insider%.)

### Честные блокеры + обход
- **X API платный** ($0.20/пост) → анонсы из **Telegram (Telethon, free/legal)**; X только burner+twscrape (ToS-грей, деприоритет).
- **Слабый ПК** — весь стек CPU/keyless (Trafilatura/sumy/GoPlus/RugCheck = JSON); decision-LLM = облако (Яндекс) или локаль 3-7B; torch только опц. (LLMLingua-2).
- **Tree of Alpha free = задержка** vs платный — для карточки-аналитика норм (не HFT).
- **Telethon userbot / twscrape = ToS-грей** — read-only по своим каналам, burner-номера.
- НЕ брать (платно/мёртво): Whale Alert, Moralis/Dune/Solscan Pro, Bitquery, snscrape, Nitter, Discord self-bots, Jina ReaderLM (3GB VRAM), Firecrawl self-host (AGPL).

### Порядок обвязки V0
`news_ws → page_extract → llm_formatter → telegram → log`. Прогнать форвард на 1 источнике + BTC → потом V1 (social/on-chain/per-layer паки).
**Граница:** не трогать `.env` (сверх Yandex/TG) / `AUTO_TRADE` / прод-движок / live-config. Это research/infra под форвард-логгер — денег не касается.

---

## ОБНОВЛЕНИЯ 02.06 (из диалога — ВАЖНЫЕ уточнения)

### 🔑 Триггеры: ОПЕРЕЖАЮЩИЕ vs ЗАПАЗДЫВАЮЩИЕ (поправка трейдера)
Нельзя валить в кучу. Entry-триггер — ТОЛЬКО опережающий.
- **⏰ ЗАПАЗДЫВАЮЩИЕ (НЕ entry):** % движения, всплеск объёма, **ликвидации** — движение УЖЕ случилось, ты видишь хвост. Роль = **контекст / риск / сквиз-топливо**, не вход.
- **🚀 ОПЕРЕЖАЮЩИЕ (вот тут эдж):** (1) **новость в момент выхода** (push-WS — ловим причину ДО реакции цены); (2) **on-chain** (кит/дев двигает кошелёк ДО толпы — a16z до HYPE, набор LAB за 36ч); (3) **календарь** (анлок/отчёт/OPEC — стоим заранее).
- Оговорка: даже новость поздняя, если **уже в цене** (урок 5-активов «известный катализатор ≠ эдж»). Ранний эдж = то, что толпа ЕЩЁ не заложила.

### 📡 Каналы (curated, два+ ведра, НЕ в кучу)
- 🟢 **АЛЬФА (триггеры):** `@markettwits` (новости РУ) · `@OKXAnnouncements` (листинги OKX) · `@NewListingsFeed` (листинги все биржи) · + добавить BWEnews/WatcherGuru (EN) · + real Whale Alert/Lookonchain (on-chain). `porter_news` — грязный (казино-реклама), парсер режет промо, опц.
- 🟡 **КОНТЕКСТ/РИСК:** `@HyperliquidLiquidations` (сквизы — запаздывающий).
- 🔴 **СЕНТИМЕНТ/КОНТРА:** памп-помойки (`*_signals/*_pumps`) — слушаем для **ДЕТЕКТА FOMO**, не для входа. Это клоны/спам (`whales_alert_liquidation_futures` = фейк Whale Alert).
- Список ЖИВОЙ — журнал покажет, чьи сигналы в плюс → пропалываем.

### 💡 Контра-счётчик шила (фича — идея трейдера, подтверждена LAB/HYPE)
Считать, **сколько памп-каналов шилят монету X** за окно. Скачок к экстриму **+ цена растянута + дивергенция** (цена вверх, упоминания вниз) → флаг **⛔ NO-GO лонг / 👀 фейд-watch**. Сам шорт — на развороте, не на хайпе (тайминг зверский). Измеримый детектор эйфории = поймал бы вершины LAB/HYPE.

### 🔐 TG-листенер (как трейдер даёт доступ)
Нужны `api_id`/`api_hash` + номер (my.telegram.org, бесплатно) — НЕ bot-token (бот каналы не читает). У трейдера в `.env` сейчас только `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` → надо добавить `TG_API_ID`/`TG_API_HASH`/`TG_PHONE` (значения — у него, я не вижу; первый код входа вводит сам → `.session` сохранится). **Старт/тест — на его основном акке (быстрее, read-only риск мал); прод 24/7 — переехать на burner** (ToS-бан не унесёт основной). `.env` правит ТРЕЙДЕР (money-guard).

### ✅ V0 ПРОГРЕСС (02.06)
- **`src/scout/page_extract.py` — СОБРАН и проверен** (Trafilatura: статья CoinDesk → title + date `2026-05-29` + 4053 симв. чистого текста, мусор/реклама срезаны). Первый кирпич петли готов.
- **V0 НЕ требует TG-акка:** источник = scout-RSS (надёжен), доставка = бот (есть), аналитик = Яндекс (есть). Tree of Alpha WS = V1-апгрейд скорости (keyless подтверждён ✓, но free-tier с задержкой + протокол недодокументирован).
- **Следующий шаг:** `scanner_v0.py` — связать RSS-новость → page_extract → llm_formatter → telegram → лог = первая живая карточка end-to-end.

---

## ОБНОВЛЕНИЯ 02.06 (вечер — заточка дизайна, НЕ переделка; скелет тот же)

> 6 правок из дизайн-разговора. Это детализация/заточка уже существующих блоков, кости не тронуты.

### 🗓️ Деление новостей: «будет / произошло» (в КАЖДОМ слое)
Заточка опережающего триггера. Каждый источник под актив делим на два под-потока:
- **📅 БУДЕТ (anticipatory):** анонсы будущего/календарь (анлок, отчёт, OPEC, листинг, окно релиза модели). → строит **вотчлист ожидаемых катализаторов** + хранит **ОЖИДАНИЕ** (консенсус/вероятность: Polymarket-%, дата, ожидаемое направление). Тут — пре-позиционирование.
- **💥 ПРОИЗОШЛО (realized):** событие случилось. → **это ТРИГГЕР ПЕРЕ-запуска анализа.**
- Ответ на «когда пере-анализ»: **на каждое «произошло», что закрывает ожидаемое «будет».** Не по таймеру — по разрешению события.

### 🎯 Сюрприз-дельта = механизм фильтра «в цене» (новый кусок)
На «произошло» система сравнивает **ФАКТ vs хранимое ОЖИДАНИЕ** (из «будет»):
- произошло **как ждали, в срок** → в цене → **NO-GO / sell-the-news**;
- произошло **раньше / крупнее / иначе** → **СЮРПРИЗ → GO**.
- Эдж живёт **в ЗАЗОРЕ** между «будет» и «произошло». Без хранимого «будет» сюрприз не измерить → делить обязательно.
- **Механизм:** таблица **pending-событий** `{актив, событие, дата, ожидание, ожид.направление}`. Входящее «произошло» матчит pending-строку → закрывает → пере-анализ с дельтой сюрприза → карточка. Это операционализирует наш главный тезис «эдж = сюрприз vs консенсус».

### 🧬 Матрица слоёв — ЧТО анализируем (детализация source-паков)
| Слой | Источники | Данные/сигналы | Кошельки? |
|---|---|---|---|
| 1. Крипта-история | агрегаторы/ETF/регуляторка | **потоки ETF**, доминация, funding/OI, уровни | ✅ киты/биржевые потоки |
| 2. Альты/мемы | листинг-каналы/TG-альфа/соцсети | **on-chain: инсайдер%/red-flag**, накопление, перелив, шилл-счётчик | ✅✅ критично |
| 3. Металлы | макро/Китай/шахты | запасы(LME/COMEX/Шанхай), спрос Китая, добыча, доллар/ставки, тарифы | ❌ |
| 4. Ресурсы | геополитика/OPEC/EIA | OPEC-решения, геособытия, запасы EIA, танкеры | ❌ |
| 5. Акции+перпы | earnings/SEC EDGAR/релизы | консенсус отчёта, IPO-таймлайн, окно релиза, **OKX-rebase** | ❌ (перпы: rebase) |
**🔑 Кошельки/on-chain — ТОЛЬКО крипта (слои 1-2).** Металлы/нефть/акции = макро+геополитика+отчёты, кошельков НЕТ. Общий — тонкий макро (Fed/доллар/risk-off).

### 📋 WATCHLIST (просканирован OKX 02.06 — всё на бирже)
```
L1 крипта:    BTC ETH SOL BNB XRP
L2 альты/мем: ОТКРЫТЫЙ (триггер-фид, НЕ фикс-список — монета приходит листингом/соц/on-chain)
L3 металлы:   XAU XAG(серебро) XPT(платина) XPD(палладий)   [OKX perps; медь — вне OKX, веб]
L4 ресурсы:   CL(нефть WTI) NG(газ)                          [OKX perps; Brent — через новости/контекст]
L5 акции:     чипы:    NVDA AMD AVGO TSM INTC MU QCOM ARM MRVL
              бигтех:  MSFT GOOGL META AMZN AAPL TSLA NFLX
              крипто-прокси: COIN MSTR HOOD PLTR    ETF: QQQ SPY    софт: CRWD NOW ORCL
              pre-IPO: OPENAI ANTHROPIC SPACEX
```
Списки маленькие нарочно (селективность). Кураторские — трейдер правит. **Почти всё на одной бирже OKX = один data-пайплайн** (исключения web: медь/Brent/газ).

### 🔗 Корреляция акций (посчитана по данным OKX, 22 акции/59 дней)
- **НЕ монолит** (средняя попарная 0.27, не «жёстко связано»).
- Кластеры: **ETF**(QQQ/SPY +0.94) · **крипто-прокси**(COIN/MSTR/HOOD +0.75…0.80 = двигаются с **БИТКОМ**, не AI) · **чипы~индекс**(TSM/AMD/QQQ ~0.7) · Marvell независим (−0.5 к MSFT).
- 🔑 **COIN/MSTR/HOOD — крипто-бета, концептуально в крипто-слой**, не «AI-акции» (ловушка: выглядят как акции, ведут как биток).
- 🔑 Корреляция = **карта РАСПРОСТРАНЕНИЯ катализатора**: событие на NVDA → рябь по чипам/AI-кластеру, но НЕ по COIN/MSTR. Сканер по кластеру: один катализатор → кто со-двигается. Оговорка: 59 дней + перп-данные шумные → реальная корр AI-акций выше.

### 🎰 Памп-режим для альт/мем (из разбора LAB — отдельный режим слоя 2)
Скам-токены (95% инсайдеров) — НЕ холд, но торгуемы как **быстрый памп с euphoria-exit**:
- red-flag-veto уточнён: не «не трогать вообще», а **«не ХОЛДить — торговать как памп с пред-заданным выходом»**;
- что увидеть: **катализатор рано** (Rewards Season за 36ч до вертикали) + структурная подпись (низкий флоат + объём + сквиз);
- **выход** = пик эйфории / слом моментума; **контра-счётчик шила = заодно exit-сигнал** (упоминания вниз при цене вверх);
- опасности (честно): руг гэпает сквозь стопы на тонкой ликвидности, конкурируем с инсайдерами, не бэктестится → **paper-first.**

---

## 🔍 ПРЕ-СТАРТ АУДИТ (рой 7 агентов, 02.06) — ЛОКАП ПЕРЕД V0

> Прогнали 6 осей по реальному коду до сборки. Вердикт: **строить МОЖНО** (`ready_to_build_v0:true`), периметр чист по коду (петля не дотягивается до ордеров), но не «из готовых кирпичей» — 2 блокера + методологические дыры, все на Claude (~день), трейдеру для V0 ничего не нужно. Эти решения **СУПЕРСЕДЯТ** более ранние формулировки выше.

### 🔴 2 блокера (Claude, код)
1. **llm_formatter НЕ drop-in.** `generate_client_text` = чарт-TA-аналитик (требует TA-снимок; промпт запрещает двигать цену от новостей; возвращает `entry_signal=='ENTRY'` — ключ auto_execute). → вынести транспорт `_call_yandex(system,user)`, написать НОВЫЙ `generate_scout_card(news,layer,trigger,market_ctx)` с GO/NO-GO-промптом; `generate_client_text` НЕ трогать/НЕ вызывать; выход без `ENTRY`/«лимитка/плечо».
2. **Журнал событий ≠ `forward_series.csv`** (тот — дневной wide, дедуп по дате; событийный — N/день по событию). → `logs/scout/scanner_journal.jsonl` append-only, ключ `card_id=sha1(url)`. Поля (валидатор не пишет без них): `card_id, ts_utc, layer, asset, trigger_type, source_url, source_ts, catalyst, in_price, red_flag, mechanics, verdict(GO/NO_GO/WATCH), side, asymmetry, invalidation, forecast, horizon_hours, price_at_decision, outcome(пусто), dedup_key, schema_version`.

### 🧮 Методология (КРИТИЧНО — грабли прошлого research)
- **baseline/excess обязателен.** `resolve_outcomes.py` (отдельно от `label_main_ws`!): по `horizon_hours` берёт forward-цену (okx/coingecko keyless) от `price_at_decision`, считает `excess = asset_ret − baseline(BTC/total_mcap за тот же горизонт)`. Метрика журнала = **excess по вердиктам**, не сырой ret. Скоринг: GO long → +excess хорошо; NO_GO/в-цене → правильно если |ret| мал; WATCH-фейд → правильно если развернулось. Активы вне OKX → `outcome_source='manual'` (трейдер дописывает).
- **anti-survivorship:** `logs/scout/pending_events.jsonl` skeleton завести СРАЗУ (`event_id, asset, event_type, expected_date, expectation_text, expected_dir, consensus_prob|null, created_at, status`) — чтобы timestamp ожидания фиксировался с начала. Наполнение/матчинг = V1+.

### 🔒 ЛОКИ V0 (разрешают противоречия выше)
- **Источник V0 = Cointelegraph RSS** (один, link прямой). `news_ws.py`/Tree-of-Alpha WS = **V1**. **CoinDesk RSS — МЁРТВ** (403/HTML, 3 прогона) — убрать из критического пути.
- **Telegram V0 = `send_message_to` на ЛИЧНЫЙ chat трейдера, НЕ broadcast** (не смешивать с продуктом-аналитиком).
- **Слой V0 = 1 (хардкод в config), trigger='rss_headline'.** Мультислой-роутер (watchlist→OKX-instId; ловушки: нефть=CL не OIL, золото=XAU не XAUT, медь/Brent/газ вне OKX) = V1.
- **Скоринг high-score = V1** (в V0 LLM на каждое отфильтрованное событие, объём мал). Формула/пороги → `config.yaml` в V1.
- **fire-on-new дедуп:** `logs/scout/scanner_seen.json` по url — ОБЯЗАТЕЛЬНАЯ часть первого кирпича (иначе спам/429 на 2-м цикле).
- **page_extract в петле:** синхронно (через `asyncio.run` на карточку), ветка деградации (`None`/`error`/пусто → «по заголовку, низкая уверенность» или скип), поставить `brotli`. requirements: `trafilatura==2.0.0, requests==2.32.5, brotli`.
- **RSS = запаздывающий:** меряет ВЕРХНЮЮ границу задержки; NO-GO/«в цене» на нём валиден, но **опережающий эдж по RSS ни доказать, ни опровергнуть** (для этого push-WS V1). Поле `source_ts` = лаг-аудит. **НЕ выносить вердикт «опережающего эджа нет» по RSS** (та же ловушка миража, что в Main).

### 🛠️ Порядок сборки (всё Claude, кроме п.10)
1. лок источника в спеке ✓(этот блок) · 2. requirements + прогон page_extract на 5-10 живых URL · 3. `_call_yandex` + `generate_scout_card` (блокер №1) · 4. схема `scanner_journal.jsonl` + writer (блокер №2) · 5. `scanner_seen.json` · 6. **`scanner_v0.py` = первая живая карточка** · 7. skeleton `pending_events.jsonl` · 8. `resolve_outcomes.py` (baseline/excess) · 9. (V0.5) docstring-guard + тест изоляции импортов · 10. **ТРЕЙДЕР для V1:** добавить `TG_API_ID/HASH/PHONE` в `.env` заранее (my.telegram.org, money-guard).

---

## ✅ AS-BUILT (03.06.2026) — что реально построено (после дип-ресёрча ядра)

> Дип-ресёрч ядра «вывода» (рой 17 агентов × Codex, сошлись ~95%) → `docs/scanner_core_SYNTHESIS_2026-06-03.md`. Собрано поэтапно, 15 коммитов, 20+ тестов, изоляция от денег цела.

**Пайплайн (реализован):**
```
ИСТОЧНИКИ: RSS Cointelegraph(LAGGING) + OKX-листинги(LEADING)   [sources/okx_listings.py]
  → INGEST-ЛОГ (всё входящее, ingest_log.jsonl — полный аудит)
  → РОУТЕР актив/слой (router.py: мульти-матч+субъект+подтверждение тикера; листинг pre-routed по инструменту)
  → МАТЕРИАЛЬНОСТЬ (event_taxonomy.yaml: режет noise_genre + ценовую болтовню + CONTEXT-фазу ДО LLM)
  → КАП 1 карточка/актив/проход
  → EVENT-ДЕДУП (dedup.py: canonical_url + RapidFuzz token_set≥88, окно 48ч)
  → ТЕМПОРАЛ будет/произошло/контекст (router.route_temporal)
  → СЖАТИЕ входа (scout_analyst._compress, extractive)
  → ПРЕДОХРАНИТЕЛЬ (GO+side только если LEADING, в коде — анти-мираж)
  → МОЗГ Qwen (scout_analyst.generate_scout_card) → карточка+график(TF под горизонт) → канал @analIIti + журнал v2
```
**Конфиги (не хардкод):** `src/scout/config/{entities,event_taxonomy,source_registry}.yaml` (активы/слои/baseline_by_layer/layer_map · материальность+noise+dedup+limits · источники/lead_class/trust).
**Логи (полный аудит):** `logs/scout/{ingest_log,scanner_journal,drops,llm_budget,scanner_outcomes}.jsonl` + `scanner_seen.json`.
**Слои:** ASML→5/QQQ, EWT→2/BTC, металлы→3/XAU, нефть/газ→4/CL; крипта-мажоры(BTC/ETH/SOL/XRP/BNB)→1/BTC.

**ОСТАЛОСЬ:**
- **Этап 3:** window-state + surprise-delta (z=факт−консенсус/σ + pre_drift). Нужны: free-key FRED/EIA (макро-консенсус) + источники; на крипте консенсуса мало → ценнее с акциями/макро. `pending_events.jsonl` пока skeleton.
- **PUSH-источник** (event-driven, как хотел трейдер): Tree-of-Alpha WS (keyless) / Telegram-листенер (Telethon, api_id трейдера). Сейчас RSS=опрос 30мин; анти-спам (дедуп по содержанию) уже есть.
- Расширение трекаемых активов/слоёв; SEC EDGAR (акции, keyless); per-layer OKX baseline для металлов/нефти/газа.
- Калибровка порогов материальности/дедупа по накопленным `drops.jsonl`.

---

## AS-BUILT (06.06.2026) - агентный scanner + Alibaba + intake buffer

Этот блок supersede-ит ранние V0-формулировки выше. Scanner уже не один LLM-разбор на
RSS, а агентная система с дешевыми layer-агентами, кодовым оркестратором и сильным chief.

### Текущий рабочий pipeline

```text
источники
  -> router.py: актив + слой + baseline
  -> event_taxonomy.yaml: materiality/noise/phase
  -> dedup.py: URL + event-signature
  -> layer_agent.py: cheap model, факты по слою
  -> orchestrator.py: кодовые правила, экономия chief-вызовов
  -> chief.py: сильная модель, GO/NO_GO/WATCH + LONG/SHORT/none
  -> Telegram: только chief-карточки
  -> logs/scout/scanner_journal.jsonl
  -> resolve_outcomes.py: outcome_long/outcome_short + mfe/mae + price_after_Nh
```

### Что реально подключено

- L1/L2/L5 получают поток: RSS Cointelegraph, Decrypt, Google News + OKX listings + SEC/DexScreener/GoPlus.
- L3/L4 получают поток: Google News metals/energy + FRED/EIA/OPEC/OilPrice; price/outcome по OKX `XAU/XAG/XPT/XPD/CL/NG-USDT-SWAP`.
- `src/scout/config/entities.yaml` расширен до 40+ активов: majors, alts/memes, акции,
  pre-IPO/perp-темы вроде SpaceX, Nvidia, Anthropic.
- `source_registry.yaml` является картой намерений `источник -> слой -> lead_class -> phase`.
  Важно: часть прямых RSS сейчас еще задана в `scanner_v0.py`; registry не единственный
  исполняемый источник правды для feed list.

### Агентная модель

- `src/utils/llm_client.py` - единый LLM-клиент.
- Провайдеры: `LLM_PROVIDER=yandex|alibaba`.
- Alibaba включается через `ALIBABA_API_KEY` и `ALIBABA_BASE_URL` OpenAI-compatible endpoint.
- Роли:
  - `cheap` - layer-агенты, массовая первичная выжимка;
  - `mid` - резервный/промежуточный тир;
  - `chief` - финальное решение по кандидатам;
  - `audit` - будущие проверки/аудит.
- На Yandex cheap временно равен сильной модели, поэтому Alibaba нужен для дешевого масштаба.

Рекомендуемый Alibaba env без реальных ключей:

```env
LLM_PROVIDER=alibaba
ALIBABA_API_KEY=sk-ws-...
ALIBABA_BASE_URL=https://<workspace>.<region>.maas.aliyuncs.com/compatible-mode/v1
LLM_CHEAP_MODEL=qwen3-30b-a3b-instruct-2507
LLM_MID_MODEL=qwen3-next-80b-a3b-instruct
LLM_CHIEF_MODEL=qwen3.7-plus
LLM_AUDIT_MODEL=qwen3.7-plus
```

Cost-log пишет `provider/model/role/tokens/cost_usd/cost_rub`. Курсы/цены можно
переопределять через `LLM_USD_RUB` и `LLM_PRICE_<MODEL>_IN_USD_PER_1M` /
`LLM_PRICE_<MODEL>_OUT_USD_PER_1M`.

### Telegram output

Текущий формат: график отправляется отдельно с коротким caption, затем текстовая
chief-карточка. Это сделано, чтобы Telegram не дублировал длинный анализ в подписи
к картинке.

В канал должны идти только chief-карточки. Дешевые NO_GO, шум, пропуски и промежуточные
агентные выводы остаются в журналах как датасет.

### SQLite intake buffer

Добавлен новый durable buffer:

```text
source item -> raw_items -> machine_docs -> normalized_events -> scanner consumer
```

Файл БД: `data/scout/news_buffer.sqlite` (gitignored).

Таблицы:

- `raw_items` - входящие новости/листинги с source metadata;
- `machine_docs` - машинно-читаемый документ после extraction;
- `normalized_events` - актив, слой, phase, materiality, event_key;
- `source_health` - базовая статистика источников.

Статусы:

```text
NEW -> EXTRACTED -> READY_FOR_AGENT -> ANALYZED
DROPPED / FAILED_RETRY / FAILED_FINAL
```

Команды:

```bash
python -m src.scout.news_buffer init
python -m src.scout.news_buffer stats
python -m src.scout.news_buffer ready --limit 5
python -m src.scout.news_buffer show <doc_id>
python -m src.scout.news_buffer resolve --limit 50
python -m src.scout.news_buffer normalize --limit 100
```

Smoke без LLM/Telegram:

```bash
python -u src\scout\scanner_v0.py --buffer --limit 0
```

Важно: `scanner.bat` переключен на `--buffer` по умолчанию. Старый прямой контур
RSS+листинги оставлен как fallback через `set USE_BUFFER=0` внутри bat.

### Что закрыто

- Система умеет видеть обе стороны: LONG и SHORT.
- Старые карточки пересчитаны по обеим сторонам: прежний long-bias был подтвержден,
  теперь это измеряется в журнале.
- Chief умеет ставить `side=short`.
- Дедуп по событию добавлен поверх URL-дедупа, чтобы одна тема не давала пачку одинаковых
  сообщений.
- Alibaba test-call и scanner-pass подтверждали реальные вызовы через `llm_provider=alibaba`.

### Открытые дыры

- L3/L4 больше не являются пустыми слоями: OilPrice/OPEC/EIA/FRED подключены, но им всё ещё нужны более быстрые breaking-источники для геополитики и сырья.
- `pending_events` и surprise-delta концептуально описаны, но полноценный календарный
  ingest еще не построен. Без него "было/будет" не даст честный surprise.
- Buffer стал default runtime для `scanner.bat`. Следующий шаг - несколько стабильных
  живых прогонов и source-quality dashboard.
- Нужен source-quality dashboard: сколько пришло, сколько извлеклось, сколько ушло в
  `DROPPED`, сколько дошло до chief, сколько реально дало движение.
