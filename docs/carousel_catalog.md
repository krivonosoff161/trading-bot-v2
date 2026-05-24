# Карусели из инфополя — реестр (тред `andrej`)

69 каруселей из DM-треда `andrej` (акк `profi.dimasik`). Скачаны через `grab_carousel.py` в
`docs/инста трасткбрикция/_carousels/<shortcode>/` (gitignore). Здесь — разбор содержимого + флаги.
Тулчейн и реестр видео — `docs/video_research_catalog.md`.

**Флаги:** 🟢 TRADE (полезно сейчас) · 🔵 CODE (кодинг / ИИ-тулзы) · 🟡 LATER (полезно позже/к фазе) · ⚪ INTEREST (другое, но интересное) · ❌ SKIP (мусор/аниме/промо)

**Метод:** проход 1 (черновой) — разбор по обложке. Потом детальный проход 2 + финальное распределение.
**Разобрано: 41 / 69.** ⭐ = особо релевантно нам.

> ## ▶ ПРОДОЛЖИТЬ В НОВОМ ЧАТЕ (проход 1, осталось ~28)
> В прошлой сессии чтение картинок упёрлось в кумулятивный лимит API (~40 изображений в контексте →
> любой image-запрос режется по ≤2000px). В **свежем** чате чтение снова работает.
> - Дочитать обложки строк со статусом ⏳ (это **#41–69 кроме #51**).
> - Читать **уменьшенные** копии: `docs/инста трасткбрикция/_covers_small/<shortcode>.jpg` (уже ≤1400px).
> - **Читать малыми батчами (~8–10 за раз)**, чтобы снова не упереться в лимит за сессию.
> - **#3 (DKXjNxuCA6L) и #20 (DUiUvnbglVz)** — видео-карусели, обложки в `_covers_small` нет; глянуть
>   первый кадр из `_carousels/<sc>/` или пометить как video-карусель.
> - Проставить флаги в таблицу, обновить счётчик. Затем — **проход 2 (детально)** + распределение.

| # | shortcode | флаг | что это |
|---|---|---|---|
| 1 | DJ4Ae0tokIp | 🔵 CODE | No-code/AI-стек «запуск компании за 0₽» (Durable, Gumroad, Make) |
| 2 | DJ6_Wn-MCEe | ❌ SKIP | Топ аниме |
| 3 | DKXjNxuCA6L | ⏳ | видео-обложка — отдельно (video-карусель) |
| 4 | DLJDmyxI6GR | ⚪ INTEREST | Медчекап / здоровье |
| 5 | DLx2DqYsSwK | ❌ SKIP | Аниме (Киберпанк s2) |
| 6 | DMhUqUjgFqi | ⚪ INTEREST | ООО / защита активов через траст/фонд |
| 7 | DNLSNCrRTFa | ⚪ INTEREST | Оффшоры / налоговая оптимизация |
| 8 | DNN8_qmI99S | 🔵 CODE | ИИ-инструменты 2025: платные vs бесплатные |
| 9 | DNiI8gVIsOu | ⚪ INTEREST | ИИ-промо (Gemini, тонко) |
| 10 | DOq-XjgDEHJ | ❌ SKIP | Аниме рейтинги |
| 11 | DPHvWB3CiTu | ⚪ INTEREST | Бесплатные курсы Stanford (самообучение) |
| 12 | DPLMuDNjKen | 🔵 CODE | ChatGPT-промпт «хакер идей» |
| 13 | DRFml93jAIR | ⚪ INTEREST | HTX — «толерантная» биржа к трафику |
| 14 | DRbUlUKgqzd | ⚪ INTEREST | РФ законопроект (бюджетный кодекс) |
| 15 | DRhXv_JDcfs | 🔵 CODE | n8n: узел Webhook (автоматизация) |
| 16 | DRkmqaBDCvq | ⚪ INTEREST | Здоровье (микоплазменная пневмония) |
| 17 | DTD8-sGDTv1 | ⚪ INTEREST | Налоговая схема «дружественный лизинг» |
| 18 | DU2t6PbDcfU | 🔵 CODE | Промпт «персональный крипто-аналитик» |
| 19 | DUDHtLRiBvv | ❌ SKIP | Игра (Arab/ARC Raiders) |
| 20 | DUiUvnbglVz | ⏳ | видео-обложка — отдельно (video-карусель) |
| 21 | DUlO4aEDJbU | ⚪ INTEREST | 6 безопасных мессенджеров |
| 22 | DV8qasfDWXx | 🔵 CODE ⭐ | Шаблон CLAUDE.md от Boris Cherny |
| 23 | DWG4gWnmihX | 🟢 TRADE | Паттерны продолжения (TA) |
| 24 | DWMNORfgRtB | ⚪ INTEREST | ФНС/ЭЦП/ГОСТ (РФ бухгалтерия) |
| 25 | DWTZmAvF5Rh | 🟢 TRADE | Статистика по активам / тайминг сессий (UTC+3) |
| 26 | DW_9mFOkeYu | 🔵 CODE ⭐ | «Claude перечитывает всё» — оптимизация токенов |
| 27 | DW_HggbGjcP | 🟢 TRADE | VIX индекс страха (макро-фильтр; ср. BACKLOG VIX+COT) |
| 28 | DWgT_Izl9Cx | 🔵 CODE | Промпт «поиск паттернов» (квант-ресёрч акции) |
| 29 | DWijbySGQb- | 🔵 CODE ⭐ | Claude Code структура проекта (.claude/commands/skills/agents) |
| 30 | DWlaW9ljHr6 | 🔵 CODE | Claude Code теряет контекст каждую сессию |
| 31 | DWs9CNpEybb | 🔵 CODE | Claude Code на других моделях (Kimi K2 / Ollama) |
| 32 | DWykcQtmvU9 | 🟢 TRADE | Профиль объёма / POC (ср. BACKLOG Volume Profile) |
| 33 | DX1brxQDWXy | 🟢 TRADE | Гл. ошибка новичка: риск / плечо / margin call |
| 34 | DX2twvWDzTO | 🟢 TRADE | ORB стратегия (15min ORB + VWAP) |
| 35 | DX35avTiPrN | 🔵 CODE | Google Jules / AI-агенты для кода (новость) |
| 36 | DX68WuOFFLf | 🟢 TRADE | 15min ORB (NY session momentum entry) |
| 37 | DX6lrdKlXcr | 🟢 TRADE | Совмещение таймфреймов (MTF) |
| 38 | DX9bMibGnk- | ⚪ INTEREST | Цветовая схема графика TradingView |
| 39 | DX9xEhhjjWA | 🟢 TRADE | ICT/SMC: BOS / IDM / OB / Liquidity grab |
| 40 | DXEtwqpFYn8 | 🔵 CODE ⭐ | graphify: память Claude + экономия токенов 71.5× |
| 41 | DXGNidNGM8X | ⏳ | _covers_small/DXGNidNGM8X.jpg (20 имг) |
| 42 | DXJ1JyjGsh0 | ⏳ | _covers_small/DXJ1JyjGsh0.jpg |
| 43 | DXKUmmzgh3u | ⏳ | _covers_small/DXKUmmzgh3u.jpg |
| 44 | DXMCCUwjPyi | ⏳ | _covers_small/DXMCCUwjPyi.jpg (1 имг) |
| 45 | DXTyl08lvhZ | ⏳ | _covers_small/DXTyl08lvhZ.jpg |
| 46 | DXW3YSKmkTm | ⏳ | _covers_small/DXW3YSKmkTm.jpg |
| 47 | DXXPB_BiIxb | ⏳ | _covers_small/DXXPB_BiIxb.jpg |
| 48 | DXZ1RK4As1A | ⏳ | _covers_small/DXZ1RK4As1A.jpg |
| 49 | DXdYrFYE9jR | ⏳ | _covers_small/DXdYrFYE9jR.jpg (20 имг) |
| 50 | DXgbeB_Dc8H | ⏳ | _covers_small/DXgbeB_Dc8H.jpg |
| 51 | DXjlj-GDXBJ | 🔵 CODE | «Stop using MCP, start using CLIs» (инфографика, опознано в тема25) |
| 52 | DXkC38AiEV9 | ⏳ | _covers_small/DXkC38AiEV9.jpg |
| 53 | DXkMmvzjPdE | ⏳ | _covers_small/DXkMmvzjPdE.jpg (1 имг) |
| 54 | DXo_RhpiJ54 | ⏳ | _covers_small/DXo_RhpiJ54.jpg |
| 55 | DXqa7GdDA-V | ⏳ | _covers_small/DXqa7GdDA-V.jpg (1 имг) |
| 56 | DXwU6JoAtmT | ⏳ | _covers_small/DXwU6JoAtmT.jpg |
| 57 | DXxAsiXDQ9L | ⏳ | _covers_small/DXxAsiXDQ9L.jpg |
| 58 | DYAKGIgFWvs | ⏳ | _covers_small/DYAKGIgFWvs.jpg |
| 59 | DYCUgFIlRDb | ⏳ | _covers_small/DYCUgFIlRDb.jpg |
| 60 | DYCnJx3DCMZ | ⏳ | _covers_small/DYCnJx3DCMZ.jpg |
| 61 | DYIFYQ5DLyH | ⏳ | _covers_small/DYIFYQ5DLyH.jpg |
| 62 | DYNZRSgmcvQ | ⏳ | _covers_small/DYNZRSgmcvQ.jpg |
| 63 | DYP4OZqDOhw | ⏳ | _covers_small/DYP4OZqDOhw.jpg |
| 64 | DYSjKxRM88R | ⏳ | _covers_small/DYSjKxRM88R.jpg (1 имг) |
| 65 | DYTla2vjIO3 | ⏳ | _covers_small/DYTla2vjIO3.jpg |
| 66 | DYVFh60kRD8 | ⏳ | _covers_small/DYVFh60kRD8.jpg |
| 67 | DYX1_-RmfP1 | ⏳ | _covers_small/DYX1_-RmfP1.jpg |
| 68 | DYdMvIQjQfM | ⏳ | _covers_small/DYdMvIQjQfM.jpg |
| 69 | DYkMCh0GnJ8 | ⏳ | _covers_small/DYkMCh0GnJ8.jpg (smart__capital, 5 имг) |

## Промежуточный итог прохода 1 (41/69)
- 🟢 TRADE: 10 · 🔵 CODE: 14 · ⚪ INTEREST: 12 · ❌ SKIP: 4 · ⏳ осталось: 29 (вкл. 2 видео-обложки)
- Кластеры на будущее: ORB/сессионные уровни (#34,36,25 + видео тема10/22), SMC/паттерны (#23,39),
  макро/объём (#27,32 — ср. BACKLOG VIX+COT, Volume Profile), Claude-воркфлоу ⭐ (#22,26,29,40).
