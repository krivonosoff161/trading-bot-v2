# Бриф для GPT #3: TRENDING×SWING — majors vs alts симуляция

> **Контекст:** в финальном отчёте `docs/gpt_full_research_18_05_2026.md` ты нашёл главную причину регрессии: универсум сменился (archive=все мажоры, live=0 мажоров), плюс новый veto `min_vol_ratio_trending=1.5` (commit 2ea6a42) резал бы archive trades n=14 WR=78.6% +0.16R.
>
> **Что хочется проверить:** работают ли новые фильтры ws_main_screener на майорах в принципе, или зажаты так что режут edge везде. Это нужно понять ДО того как трогать `prefilter_*` или `min_vol_ratio_trending`.

## Задача

Симулятивно применить **текущие фильтры live ws_main_screener** к **archive майорам** (BTC/ETH/SOL/XRP/DOGE) на TRENDING×SWING bucket. Сравнить:

- **archive trades AS-IS** (что сигналил старый ws_scanner, без новых фильтров)
- **archive trades AFTER NEW FILTERS** (что остался бы если применить новые правила)

## Фильтры которые нужно применить (текущие prod)

Из `config.yaml` и `src/strategy/signal_engine.py`:

1. **prefilter_vol_ratio_min: 1.0** + **prefilter_adx_min: 10** — оба должны выполняться, иначе свеча 15m отбрасывается до compute_signal
2. **min_vol_ratio_trending: 1.5** — hard veto для TRENDING режима (commit 2ea6a42)
3. **TRENDING SWING short oversold veto** (commit 57ec2df) — блокирует SELL при глубоко oversold RSI
4. **slope_min: 35** — если применимо к bucket-у
5. **trending_require_fvg: false** (текущий config)

## Что измерить

Для archive TRENDING×SWING bucket (24 trades, все мажоры):

```
| Metric           | AS-IS | AFTER NEW FILTERS | Delta |
|------------------|-------|-------------------|-------|
| n                | 24    | ?                 | -X    |
| WR               | 75.0% | ?                 | ±YY pp|
| avg_R            | +0.09 | ?                 | ±0.YY |
| std_R            | ?     | ?                 |       |
| PF               | ?     | ?                 |       |
| max_DD           | ?     | ?                 |       |
```

И отдельно — **какие именно сделки выпали и по какому фильтру** (счётчик: prefilter=N1, min_vol_ratio=N2, oversold_veto=N3).

## Дополнительно

Если есть возможность — **тот же тест для FAST×DRIFT и TRENDING×FAST** на archive majors. Хочется убедиться что новые фильтры не убивают и эти прибыльные bucket-ы тоже.

## Сценарий итогового вердикта

**A.** Если новые фильтры на archive majors режут <20% сделок и avg_R остаётся положительным → фильтры ок, проблема в alts универсуме. Решение: pinned majors + ждать live данные.

**B.** Если новые фильтры режут >30% сделок ИЛИ avg_R становится отрицательным → фильтры зажаты. Решение: рассмотреть откат `min_vol_ratio_trending` к 1.2 или удалить hard veto.

**C.** Промежуточный случай (20-30% cut, avg_R слегка положительный) → нужны дополнительные данные, возможна частичная корректировка одного фильтра.

## Формат отчёта

Создать `docs/gpt_majors_vs_alts_19_05_2026.md`:

1. **Методология** (1 параграф) — какие фильтры применил, как симулировал
2. **Таблицы AS-IS vs AFTER** для трёх bucket-ов (TRENDING SWING, FAST DRIFT, TRENDING FAST)
3. **Breakdown filter cuts** — счётчик по причинам
4. **Вердикт A/B/C** — какой сценарий по результатам
5. **Concrete next experiment** — что делать в коде/конфиге (или "пока не трогать")

## Что НЕ делать

- Не модифицировать prod код в `scripts/ws/*.py` и `src/strategy/*.py`
- Не трогать `config.yaml`
- Не пытаться "восстановить" фильтры — только анализ

## Время

Оценочно 1-2 часа работы. Не блокирующее — мы параллельно делаем pump-фиксы.

После твоего отчёта — обсуждаем стоит ли менять main scanner config или ждать накопления pinned majors данных.
