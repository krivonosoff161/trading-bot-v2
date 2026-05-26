# GPT early-entry findings — 2026-05-26

Статус: research, read-only. Продовые конфиги, `.env`, `AUTO_TRADE`, start/stop и live-движки не трогались.

## Короткий вывод

Скринер опаздывает не случайно, а структурно: текущий `ENTRY` включается после набора подтверждений импульса
(`vol_ratio_sig`, растущий ADX, цена по правильную сторону EMA/VWAP, ускоряющийся slope). Это хорошо выбирает
направление, но момент входа оказывается фазой "подтвержденного пробоя", а не фазой "откат исчерпан".

Минимальный кандидат для следующего бэктеста: **не заменять direction-анализатор**, а добавить ранний
`ENTRY_CANDIDATE` внутри уже выбранного направления: цена сделала контролируемый откат к 5m EMA/уровню,
RSI ушел в exhaustion против направления, объем не является climax, диапазон не расширяется, затем появляется
первый бар остановки/разворота. Это не старое правило-надстройка: триггер должен жить до `ENTRY` скринера и
становиться самим моментом решения, а не фильтром после позднего сигнала.

## Что пересчитано

1. Перечитаны обязательные документы: `CLAUDE.md`, `docs/AI_CONTEXT.md`, `docs/geometry/findings.md`,
   `docs/gpt_task_early_entry_26_05_2026.md`.
2. Проверен код `compute_signal()` в `src/strategy/signal_engine.py`.
3. Запущены существующие scripts:
   - `entry_timing_audit.py`: на позднем майском куске IDEAL был на `-57 мин` к FIRE.
   - `signal_multivariate.py`: `vol_ratio` сохраняет отрицательный частный эффект, `di_dir` и `bb_expanding`
     в основном тени `adx_1h`.
   - `entry_rule_sim.py`: правило откат-разворот ловит больше MFE, но это не NET-доказательство.
4. Добавлен черновой read-only script:
   `scripts/analysis/research/early_entry_causal_scan_26_05_2026.py`.
   По умолчанию он использует свечи; тейп включается отдельно через `EARLY_ENTRY_INCLUDE_TAPE=1`, чтобы не
   блокировать обычный прогон тяжелым I/O.

## Факты из новых расчетов

Новый candle-only scan по `logs/signals/signal_snapshot.jsonl` и `logs/features/5m`:

| Метрика | FIRE | IDEAL diagnostic | Сдвиг IDEAL-FIRE |
|---|---:|---:|---:|
| n | 104 | 104 | 2 сигнала без 5m данных |
| offset IDEAL | — | mean `-35.0 мин`, median `-57.5 мин` | раньше |
| `pullback_ema_pct` | `-0.826` | `+0.852` | `+1.678` |
| `rsi_exhaust` | `-13.950` | `+4.937` | `+18.887` |
| `slope6_dir` | `+5.191` | `-6.948` | `-12.138` |
| `vol_ratio_5m` | `1.631` | `1.394` | `-0.237` |

Интерпретация side-normalized:
- FIRE обычно **растянут за EMA по направлению сигнала** и RSI уже не в зоне отката.
- IDEAL diagnostic чаще находится **на откате/ниже EMA для long или выше EMA для short**, RSI экстремален
  против будущего входа, локальный slope еще против направления или только начинает тухнуть.
- Объем на FIRE выше, но не является положительным причинным признаком.

Минимальный candle-only draft из нового scan:

```text
0.15 <= pullback_ema_pct <= 2.50
AND rsi_exhaust >= 2
AND 0.45 <= vol_ratio_5m <= 2.20
AND range_ratio <= 1.35
AND (body_dir_pct > 0 OR close_prev_dir_pct > 0)
```

На pre-FIRE candidate bars: `63` срабатывания из `2543`, средний future favourable move `+3.289%`,
median `+2.456%`, средний offset `-61 мин`; FIRE baseline `+1.937%`, median `+0.813%`.
Это **только MFE/favourable diagnostic**, не заявка на прибыль.

## Почему скринер опаздывает

Текущий `compute_signal()` выбирает направление и момент одним большим gate:

- TRENDING SWING/FAST требуют `adx_1h_rising` и `vol_ratio_sig >= cfg["vol"]`
  (`src/strategy/signal_engine.py:1018`, `src/strategy/signal_engine.py:1028`).
- RANGING тоже требует `vol_ratio_sig >= cfg_r["vol"]` (`src/strategy/signal_engine.py:1048`).
- DRIFT требует направление EMA/VWAP и `vol_ratio_sig >= cfg_d["vol"]`
  (`src/strategy/signal_engine.py:1074`, `src/strategy/signal_engine.py:1078`).
- После выбора режима добавляется ускоряющийся slope в сторону сделки:
  `sl_cur >= slope_min and sl_cur > sl_prev` для buy, зеркально для sell
  (`src/strategy/signal_engine.py:1092-1097`).
- Финальный `ENTRY` появляется только если не сработали поздние veto/risk gates
  (`src/strategy/signal_engine.py:1211-1216`).

Это архитектурно заставляет ждать "доказательства", что движение уже идет: объем вырос, ADX растет, slope
ускоряется, цена уже по нужную сторону EMA. Именно поэтому direction часто верный, а цена входа поздняя.

## Гипотезы раннего входа, по силе

### H1. Exhausted pullback retest внутри уже выбранного направления

Суть: direction остается из текущего анализатора, но момент входа ищется раньше: откат к 5m EMA/VWAP/последнему
локальному уровню, RSI-истощение против будущего направления, затем первый бар остановки.

Проверяется:
- `logs/features/5m`: `close`, `ema20`, `rsi`, `open/high/low/close`, `volume_usdt`, `vol_ratio_sig`.
- `logs/features/15m/1H`: режим, ADX/DI, bias, VWAP/day_position.
- Май: tick fill по `E:\trading-data\ticks`.
- Апрель: 1m/5m OKX candles, без тейп-усилителей.

Ожидаемый эффект: сдвиг входа на `30-60 мин` раньше FIRE и снижение покупки/продажи на climax-bar.

Риск: MFE может улучшиться, но adverse первым убьет NET. Поэтому backtest обязан считать fill, SL-first/реальный
путь и комиссии; IDEAL использовать только как diagnostic label.

### H2. Volume climax veto как часть момента, а не пост-фильтр

Суть: высокий объем не подтверждение входа, а признак поздней фазы. В мультиварианте `vol_ratio` выжил как
отрицательный частный эффект: single `r=-0.16`, partial beta `-0.38`; с контролем regime/style beta `-0.32`.
Дерево из `signal_multivariate.py`: `vol_ratio > 3.40` дает плохой класс с весами `[16,3]`.

Проверяется:
- `context.vol_ratio_sig` из snapshots.
- 5m `volume_usdt` / baseline.
- Отдельно bucket `0.45-2.20`, `2.20-3.40`, `>3.40`.

Ожидаемый эффект: меньше входов на исчерпанном breakout confirmation. Это должно пережить OOS лучше, чем
майское правило, потому что не зависит от конкретного RSI threshold, а совпадает с устойчивым негативным
мультивариантным эффектом.

Риск: жесткий cap может выкинуть настоящие трендовые continuation. Поэтому cap должен быть режимным:
для DRIFT жесткий, для TRENDING только если есть растяжение за EMA и нет ретеста.

### H3. Order-flow absorption / CVD divergence на уровне ретеста

Суть: на откате агрессор давит против будущего направления, но цена перестает продавливать уровень.
Для long: sell CVD остается отрицательным или усиливается, а low перестает обновляться / быстро выкупается.
Для short: зеркально.

Внешняя опора: order-flow подходы используют tape/CVD/absorption для проверки реального участия и разворотов;
Bookmap описывает CVD и absorption как инструменты для подтверждения истощения и реакции цены на поток ордеров
[Bookmap order-flow strategies](https://bookmap.com/en/content/order-flow-strategies). Академическая микроструктура
также поддерживает идею, что order-flow imbalance связан с price formation, особенно при учете нескольких уровней
книги [arXiv:1907.06230](https://arxiv.org/abs/1907.06230).

Проверяется:
- Май: `E:\trading-data\ticks/{SYM}/{date}.csv.gz`, поля `side/price/size`.
- Derived: `cvd_5m_dir`, `price_progress_5m`, `absorption = cvd_dir - normalized_price_progress`,
  `delta_flip` на последней минуте.
- Апрель: недоступно как tick-фича, значит OOS можно делать только candle-proxy; tick-версия проверяется
  как May-only enhancement, не как обязательное условие для апрель/май сравнения.

Ожидаемый эффект: отсеять "нож" после откатного входа. Если H1 дает MFE, H3 должен улучшать NET.

Риск: тейпа нет до 11.05, поэтому нельзя объявлять это OOS-устойчивым без нового forward или proxy.

### H4. Retest of breakout/FVG/structure, а не сам breakout

Суть: внешний подход "breakout retest" применим только как механика уровня, не как покупка breakout candle.
Типовой breakout с объемом и close above/below часто ждет подтверждения; retest дает более раннюю/лучшую цену
после возврата к уровню. TradeAlgo описывает breakout как проход уровня с volume expansion и отмечает retest
как вторичный вход после пробоя [TradeAlgo breakout guide](https://www.tradealgo.com/trading-guides/stocks/breakout).

Проверяется:
- Уровень: последний 15m swing high/low, 5m mini range high/low за `6-12` баров, 15m FVG gap если есть.
- Retest: цена вернулась к уровню в пределах `0.0-0.35 ATR_5m` / `0.15-0.75%`.
- Failure guard: не входить, если close закрепился обратно за уровень против thesis.

Ожидаемый эффект: сохранить direction edge, но заменить "close на подтверждении" на "retest после первого
структурного сдвига".

Риск: если уровень выбран по будущему swing, это look-ahead. Уровни должны быть только из уже закрытых баров.

### H5. Volatility compression before continuation

Суть: ранний вход должен происходить до расширения диапазона, когда диапазон/volume еще не climax.
В новом scan `range_ratio` у IDEAL чуть выше FIRE, но correlation слабая; это слабее H1-H3.

Проверяется:
- 5m `range_ratio = current_range / avg_range_12`.
- `bb_width_pct` и `bb_expanding` на 15m.
- Запрет "wide thrust bar" как entry-bar.

Ожидаемый эффект: убрать поздние thrust bars.

Риск: слабая самостоятельная сила; использовать только как guard, не как основной триггер.

## Почему майский DRIFT плюс не пережил апрель

Вероятнее всего, причина смешанная:

1. **Конфиг и популяция разные.** Апрельский скринер DRIFT-heavy и слабее по FIRE baseline; майская выборка
   уже после изменений и с тик-точными fills.
2. **Методика fills разная.** Май считает реальные принты агрессора; апрель через 5m свечи и консервативный
   порядок stop-first.
3. **Старое правило ловило разворот отката, но не проверяло absorption.** Поэтому в мае на DRIFT оно стало
   "менее плохим", а в апреле adverse/нож съел edge.
4. **Малый n.** Майский эффект сидел в конкретном режиме и периоде; сам факт "MFE лучше" устойчивее, чем
   "NET положительный".

Устойчивый признак для обоих периодов должен быть candle-first: `pullback-to-EMA/level + RSI exhaustion +
no volume climax + no wide thrust`, а tick absorption должен быть отдельным May/forward усилителем.

## Спека минимального causal-trigger кандидата

Название: `EARLY_RETEST_EXHAUSTION_V1`.

Входной бар: закрытый 5m бар `t`, находящийся не позже текущего FIRE и не раньше `fire-24` в research;
в live это любой новый закрытый 5m бар после того, как direction analyzer уже имеет directional thesis
(`side`, `regime`, `trade_style`) без финального `ENTRY`.

Обязательные условия:

```text
direction_thesis:
  side/regime/style taken from current analyzer, but before final ENTRY gate
  regime in {DRIFT, TRENDING}; RANGING excluded in V1

pullback:
  0.15 <= -sgn * (close_5m / ema20_5m - 1) * 100 <= 2.50
  OR abs(close_5m - retest_level) <= 0.35 * atr_5m

exhaustion:
  rsi_exhaust = (50-rsi_5m for buy, rsi_5m-50 for sell) >= 2

no_climax:
  0.45 <= vol_ratio_5m <= 2.20
  context.vol_ratio_sig <= 3.40
  range_ratio_5m <= 1.35

first_turn:
  sgn * (close_5m - open_5m) > 0
  OR sgn * (close_5m - prev_close_5m) > 0

anti_late:
  sgn * (close_5m / ema20_5m - 1) * 100 <= 0.40
  day_position not in final 10% against risk:
    buy: day_position <= 0.90
    sell: day_position >= 0.10
```

Optional May/forward tick enhancer, not for April OOS:

```text
absorption_confirm:
  last_5m tape has aggressive flow against side but price progress no longer follows
  OR last_60s delta flips in side direction after pullback low/high
```

Backtest protocol:

1. Май: use tick tape for fill/order path, same geometry grid as `entry_full_backtest.py`.
2. Апрель: use OKX 1m/5m candles; conservative stop-first if path unknown.
3. Report four numbers separately: all NET, matched delta vs FIRE, DRIFT-only, TRENDING-only.
4. Reject if:
   - all NET <= 0 in either period;
   - matched delta positive but absolute NET negative in both periods;
   - trigger count collapses below usable sample;
   - effect exists only with tick-only enhancer.

## Отличие от отвергнутой надстройки

Старое правило было "после события бота ищем лучший вход на тех же событиях". Новый подход должен быть
встроен **перед финальным `ENTRY`**: direction analyzer может сказать "сторона buy/sell уже валидна", а
orchestrator ищет момент `EARLY_RETEST_EXHAUSTION` вместо ожидания `vol_ratio + slope + ADX confirmation`.

Ключевое отличие: старое правило использовало RSI-extreme + reversal + not-extended как standalone entry.
Новая спека добавляет:
- explicit anti-climax (`vol_ratio_5m`, `context.vol_ratio_sig`, `range_ratio`);
- retest level/EMA как причинный уровень, а не просто "не растянут";
- режимный scope: V1 без RANGING;
- optional absorption для борьбы с adverse-first ножом;
- обязательный апрель/май протокол, где tick-only признаки не имеют права быть единственным edge.

## Практический следующий шаг

Сделать отдельный backtest script `entry_early_retest_v1.py` в `scripts/analysis/research/`:
- взять direction thesis из существующих snapshots/archive signals;
- на каждом 5m баре до FIRE искать `EARLY_RETEST_EXHAUSTION_V1`;
- считать NET, не MFE;
- вывести matched FIRE vs EARLY, май tick и апрель candle отдельно.

До положительного NET на обоих периодах это не конфиг и не торговый сигнал.
