# GPT audit report: bot behavior 16-18.05.2026

Дата аудита: 2026-05-18  
Источники: `logs/pump/pump_signals.jsonl`, `logs/pump/pump_labels.jsonl`, `logs/pump/ws_pump_orchestrator.log`, `logs/signals/main_signals*.jsonl`, `config.yaml`, `scripts/ws/ws_pump_orchestrator.py`, `logs_archive/09.05.2026/pump/`, `E:\trading-data\ticks\`.

## Executive summary

Главные находки пользователя в целом подтверждаются, но с уточнениями:

1. **Breakeven/trailing - главный численный рычаг.** На 16-18.05 база pump = **-19.98% net**. Симуляция "если SL-сделка уже имела MFE >= 1.0%, то выход по entry gross / -0.10% net fee" дает **+20.36 п.п.** к результату и переводит период почти в ноль: **-19.98% -> +0.38%**. Правило `mfe_r >= 1.0` дает похожий эффект: **+21.56 п.п.**, **-19.98% -> +1.58%**.
2. **Path B действительно обходит 2nd-candle confirmation и ухудшает результат.** За 16-18.05 найдено 3 Path B входа, все 3 закрылись SL: **0 TP / 3 SL, -3.614% net**. Все они 18.05. Отключение Path B само по себе улучшило бы 18.05 с **-15.07% до -11.45%**, но не решает MFE->SL.
3. **Permanent blacklist по BILL/BSB/RIVER/APR не стоит вводить вслепую.** По выборке 16-18.05 удаление `BSB+BILL+APR+RIVER` дает **+20.22 п.п.**, но BILL имел сильные TP 16.05 и 17.05. Более устойчивый вывод: BSB/RIVER/APR/BABY требуют временного risk-off или отдельного профиля, BILL - только временный ban после локальной серии SL.
4. **Telegram `msg_id=None` скорее всего не anti-spam, а no-op отправка.** В `src/utils/telegram.py` `send_message_to()` возвращает `None` только если `_BOT_TOKEN` пустой. Если Telegram реально ответил `ok: true`, `message_id` должен быть в `result`. В `ws_pump_orchestrator.py` нет `load_dotenv()` до импорта `src.utils.telegram`, поэтому сервисный запуск без экспортированного `TELEGRAM_BOT_TOKEN` будет писать `NOTIFY ok | msg_id=None`, ничего не отправляя.
5. **Аномалия hold=433 min найдена.** Это `RIVER-USDT-SWAP`, `signal_id=c6dbad43-0d0`, открыт 2026-05-16 13:00 UTC, закрыт 20:13 UTC, SL, net **-2.8598%**, MFE **+4.5669%**. Мониторинг не завис полностью, но позиция пережила 7h13m без protective trail.

## Data integrity notes

Есть расхождение с брифом по текущим файлам:

| Date | Brief pump trades | Actual closed labels | Actual net |
|---|---:|---:|---:|
| 2026-05-16 | 47 | 47 | -6.5343% |
| 2026-05-17 | 45 | 46 | +1.6174% |
| 2026-05-18 | 29 | 28 | -15.0664% |

Дальше все расчеты сделаны по фактическим `pump_labels.jsonl`, как было согласовано.

## Pump metrics 16-18.05

| Date | Trades | TP | SL | WR | Net | PF |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-16 | 47 | 14 | 33 | 29.8% | -6.5343% | 0.769 |
| 2026-05-17 | 46 | 18 | 28 | 39.1% | +1.6174% | 1.069 |
| 2026-05-18 | 28 | 7 | 21 | 25.0% | -15.0664% | 0.511 |
| **Total** | **121** | **39** | **82** | **32.2%** | **-19.9833%** | ~0.73 |

18.05 был не просто "жесткий рынок": WR упал до 25%, но основная просадка возникла из-за размера проигрышей после уже достигнутого MFE.

## Breakeven / trailing simulation

Метод: для SL-сделки, где `mfe_pct` достиг заданного порога, заменял фактический net на **-0.10%**. Это консервативнее, чем "0%", потому что сохраняет round-trip fee из текущей модели.

| Rule | Changed SL | Sim net | Delta vs base |
|---|---:|---:|---:|
| MFE >= 0.3% | 42 | +24.6744% | +44.6577 п.п. |
| MFE >= 0.5% | 32 | +16.6382% | +36.6215 п.п. |
| MFE >= 0.8% | 16 | +5.8168% | +25.8001 п.п. |
| MFE >= 1.0% | 10 | +0.3798% | +20.3631 п.п. |
| MFE >= 1.5% | 8 | -1.7647% | +18.2186 п.п. |
| MFE >= 2.0% | 6 | -4.3419% | +15.6414 п.п. |
| MFE >= 2.5% | 5 | -5.8844% | +14.0989 п.п. |
| MFE_R >= 1.0 | 20 | +1.5766% | +21.5599 п.п. |

По дням для практичных правил:

| Rule | 16.05 delta | 17.05 delta | 18.05 delta |
|---|---:|---:|---:|
| MFE >= 0.8% | +4.74 п.п. | +5.38 п.п. | +15.67 п.п. |
| MFE >= 1.0% | +3.87 п.п. | +2.62 п.п. | +13.87 п.п. |
| MFE_R >= 1.0 | +5.84 п.п. | +4.61 п.п. | +11.11 п.п. |

Вывод: гипотеза breakeven подтверждена. Лучший production-кандидат не `0.3%`, а **move SL to entry after 1R** или **MFE >= 0.8-1.0%**, потому что по labels невозможно проверить, не выбило бы ранний BE часть будущих TP.

Ключевые MFE->SL:

| Date | Symbol | Side | Open UTC | Hold | MFE | MFE_R | Net |
|---|---|---|---:|---:|---:|---:|---:|
| 16.05 | RIVER | DUMP | 13:00 | 433m | +4.5669% | 1.65 | -2.8598% |
| 18.05 | APR | PUMP | 01:55 | 31m | +3.7500% | 1.03 | -3.7518% |
| 18.05 | APR | DUMP | 13:30 | 94m | +3.3619% | 0.79 | -4.3617% |
| 18.05 | BSB | PUMP | 06:05 | 5m | +2.5962% | 1.39 | -1.9666% |
| 18.05 | AI | DUMP | 07:10 | 106m | +2.5021% | 1.60 | -1.6590% |

## Path A vs Path B

Классификация: в текущем коде Path A (`CONFIRM`) вызывает `_open_position(..., price_move=0.0, ...)`, поэтому `pump_signals.pct_move == 0.0`. Path B пишет реальный `pct_move`.

| Segment | Trades | TP | SL | WR | Net | Avg/trade |
|---|---:|---:|---:|---:|---:|---:|
| Path A confirm | 118 | 39 | 79 | 33.1% | -16.3693% | -0.139% |
| Path B standalone | 3 | 0 | 3 | 0.0% | -3.6140% | -1.205% |

Path B trades:

| Open UTC | Symbol | Side | pct_move | vol_ratio | Result | Net |
|---|---|---|---:|---:|---|---:|
| 2026-05-18 02:50 | BSB | PUMP | 2.170% | 2.836x | SL | -1.9575% |
| 2026-05-18 09:17 | AI | PUMP | 1.951% | 9.452x | SL | -0.7024% |
| 2026-05-18 13:33 | LAB | DUMP | 1.843% | 6.281x | SL | -0.9541% |

Вывод: Path B критичен как дефект архитектуры. Даже если выборка маленькая, это прямой bypass фильтра, а результат на текущем дне плохой.

### Diff to disable Path B

Минимальный diff: оставить код Path B на месте, но сделать его opt-in через config. По умолчанию ключа нет, значит Path B отключен.

```diff
diff --git a/scripts/ws/ws_pump_orchestrator.py b/scripts/ws/ws_pump_orchestrator.py
--- a/scripts/ws/ws_pump_orchestrator.py
+++ b/scripts/ws/ws_pump_orchestrator.py
@@
         # --- Path B: standalone candle-close detection (backup) ---
+        if not self.config.get("enable_path_b", False):
+            return
+
         if now - self.last_signal_wall.get(sym, 0.0) < float(self.config["alert_cooldown_sec"]):
             return
```

Если потом понадобится A/B тест, добавить в `config.yaml`:

```yaml
pump_orchestrator:
  enable_path_b: false
```

## Problem pairs and blacklist decision

Worst pairs 16-18.05:

| Symbol | Trades | TP | SL | Net | Dates |
|---|---:|---:|---:|---:|---|
| APR | 5 | 2 | 3 | -6.8991% | 16, 18 |
| BSB | 9 | 2 | 7 | -6.1920% | 17, 18 |
| BABY | 5 | 0 | 5 | -5.1722% | 16, 17, 18 |
| RIVER | 3 | 0 | 3 | -4.2350% | 16, 17 |
| LAB | 3 | 0 | 3 | -3.4701% | 16, 18 |
| BILL | 15 | 5 | 10 | -2.8937% | 16, 17, 18 |

Missed finding: **BABY** is worse than BILL by consistency: 5 trades, 0 TP, -5.17%.

Blacklist simulations:

| Removed | Trades removed | Sim net | Delta |
|---|---:|---:|---:|
| BSB | 9 | -13.7913% | +6.1920 п.п. |
| BILL | 15 | -17.0896% | +2.8937 п.п. |
| APR | 5 | -13.0842% | +6.8991 п.п. |
| RIVER | 3 | -15.7483% | +4.2350 п.п. |
| BSB+BILL | 24 | -10.8976% | +9.0857 п.п. |
| BSB+BILL+APR | 29 | -3.9985% | +15.9848 п.п. |
| BSB+BILL+APR+RIVER | 32 | +0.2365% | +20.2198 п.п. |
| APR+BSB+BABY+RIVER | 22 | +2.52% | +22.50 п.п. |

Рекомендация:

- **Не permanent blacklist для BILL.** У BILL были сильные TP: +2.4984%, +2.7371%, +1.3318%, +2.4663%, +1.6050%. Он плох из-за серий, а не из-за полного отсутствия edge.
- **RIVER и BABY - кандидаты на жесткий временный blacklist до накопления новой статистики.** RIVER: 0/3, включая hold 433m. BABY: 0/5.
- **BSB/APR - отдельный профиль или временный risk-off.** У APR большие MFE->SL, значит проблема может быть не вход, а exit/trailing. У BSB 18.05 оба SL, один Path B и один MFE +2.596%.
- Практический вариант: `pair_risk_overrides` вместо permanent blacklist: `size_mult=0.0` или `max_trades_per_day=1` для BABY/RIVER, `size_mult=0.5` и обязательный BE для BSB/APR/BILL.

Текущий `session_ban_sl_no_tp=3` почти не защищает 16-18.05: симуляция "ban после 3 SL без TP в UTC day" улучшила только **+0.074 п.п.**. Причина: проблемные пары либо получают TP до серии SL, либо не успевают набрать 3 SL в один UTC день.

## Tape analysis: buy_ratio / directional ratio

Доступность данных: из 121 pump-сделки за 16-18.05 tape-файлы найдены для **52 сделок**. Для остальных нет локального `E:\trading-data\ticks\<sym>\<date>.csv(.gz)`.

Метрика `align`: доля notional в сторону сделки. Для PUMP это buy%, для DUMP это sell%.

Итоги по 52 сделкам:

| Feature | Rule | Kept | WR kept | Net kept | Drop WR | Drop net |
|---|---|---:|---:|---:|---:|---:|
| pre300_60 align | >= 0.55 | 13 | 38.5% | +0.0608% | 30.8% | -1.9102% |
| pre300_60 align | >= 0.60 | 5 | 20.0% | -3.0814% | 35.3% | +1.2320% |
| pre60 align | >= 0.55 | 20 | 25.0% | -9.6261% | 42.1% | +7.7767% |
| pre60 align | >= 0.65 | 9 | 55.6% | +4.0457% | 26.7% | -5.8951% |
| post60 align | >= 0.70 | 9 | 44.4% | +1.4497% | 30.0% | -3.2991% |

Вывод: простая линейная гипотеза "чем выше pre-entry align, тем лучше" **не подтверждается**. Слабый средний align не обязательно плох, а `pre60 align >= 0.55` даже хуже. Но экстремальный `pre60 align >= 0.65` выглядит полезно: 9 сделок, WR 55.6%, +4.05%. Нужен более широкий backtest, потому что 9 сделок недостаточно для production-фильтра.

Средние признаки:

| Outcome | n | pre300_60 align avg | pre60 align avg | post60 align avg | post300 align avg |
|---|---:|---:|---:|---:|---:|
| TP | 17 | 0.511 | 0.511 | 0.620 | 0.563 |
| SL | 35 | 0.517 | 0.533 | 0.619 | 0.507 |

### Tape slice: EDEN TP, 18.05 09:15 UTC

`EDEN-USDT-SWAP`, PUMP, Path A, TP, net +1.9208%, MFE +2.0208%.

```
09:12  v-0.24%  notional= 45813  buy%=39
09:13  ^+0.21%  notional= 18109  buy%=49
09:14  ^+0.06%  notional= 23853  buy%=70
09:15  ^+0.60%  notional= 97422  buy%=58  <- ENTRY
09:16  ^+1.61%  notional= 72745  buy%=71
09:17  v-0.57%  notional= 93820  buy%=58
09:18  v-0.57%  notional=108358  buy%=54
09:19  ^+0.44%  notional= 20431  buy%=54
09:20  ^+1.34%  notional=175448  buy%=58
```

Хороший пример продолжения после входа: сильный notional в entry minute и следующий импульс 09:16 с buy%=71.

### Tape slice: DOGE SL, 18.05 06:20 UTC

`DOGE-USDT-SWAP`, DUMP, Path A, SL, net -0.4609%, MFE +0.0767%.

```
06:17  v-0.27%  notional= 1118  buy%=62
06:18  v-0.12%  notional= 1208  buy%=49
06:19  v-0.15%  notional=  901  buy%=46
06:20  v-0.24%  notional=21266  buy%=42  <- ENTRY
06:21  ^+0.02%  notional= 1364  buy%=44
06:22  v-0.01%  notional= 1026  buy%=53
06:23  ^+0.07%  notional=  971  buy%=65
06:24  v-0.02%  notional= 1043  buy%=44
06:25  ^+0.04%  notional= 2982  buy%=80
```

Паттерн из брифа подтверждается: после entry исчезает объем, затем buyers возвращаются.

### Tape slice: BSB MFE->SL, 18.05 06:05 UTC

`BSB-USDT-SWAP`, PUMP, Path A, SL, net -1.9666%, MFE +2.5962%.

```
06:02  ^+0.28%  notional= 19523  buy%=50
06:03  ^+1.01%  notional= 85037  buy%=67
06:04  ^+1.69%  notional=286669  buy%=58
06:05  ^+1.36%  notional= 93721  buy%=56  <- ENTRY
06:06  v-0.42%  notional= 47440  buy%=38
06:07  ^+0.45%  notional= 60016  buy%=52
06:08  v-0.31%  notional= 37989  buy%=50
06:09  v-0.61%  notional=147558  buy%=56
06:10  v-0.69%  notional= 79116  buy%=47
```

Здесь вход не выглядит слабым. Проблема не в buy_ratio до входа, а в отсутствии защиты после достигнутого MFE.

### Tape slice: UB instant reversal, 18.05 09:45 UTC

`UB-USDT-SWAP`, PUMP, Path A, SL, net -1.4545%, MFE 0.0%.

```
09:42  ^+1.38%  notional=6006  buy%=61
09:43  v-0.75%  notional=1221  buy%=44
09:44  ^+0.81%  notional=2810  buy%=59
09:45  ^+0.26%  notional=1545  buy%=50  <- ENTRY
09:46  v-1.33%  notional=1110  buy%=28
09:47  ^+0.09%  notional= 756  buy%=65
```

Это классический слабый entry: low notional, mixed buy%, мгновенный разворот.

## Main Screener verification

Фактические labels совпадают с брифом:

| Date | Labels | Outcomes |
|---|---:|---|
| 16.05 | 9 | 3 TP1, 1 TP2, 5 TIME |
| 17.05 | 11 | 3 TP1, 1 TP2, 4 SL, 3 TIME |
| 18.05 | 4 | 3 TP1, 1 TIME |

16.05 массовые TRENDING/TIME подтверждаются: 4 TIME в TRENDING, 1 TIME в DRIFT. Это больше похоже на внешний market-wide фактор или синхронный screener trigger, чем на баг лейблера. Практический контроль: лимитировать одновременные сигналы одного режима, например `max_new_signals_per_regime_per_15m=2`, но только после проверки, не срежет ли это 18.05 TRENDING winners.

## Runtime filters and CB behavior

По реконструкции `ws_pump_orchestrator.log`:

| Date | PENDING | SKIP confirm | CONFIRM | OPEN | CLOSE | EVICT | CB HALT |
|---|---:|---:|---:|---:|---:|---:|---:|
| 16.05 | 298 | 149 | 47 | 47 | 47 | 230 | 2 |
| 17.05 | 342 | 178 | 48 | 48 | 45 | 244 | 1 |
| 18.05 | 203 | 105 | 25 | 28 | 29 | 193 | 3 |

18.05 `CONFIRM -> OPEN` > 100% из-за 3 Path B входов.

CB работает технически корректно: 18.05 три `CB HALT` соответствуют дневной просадке ниже `cb_daily_loss_pct=4.0` и последующему reset после `cb_daily_halt_cooldown_min=120`. Но reset daily PnL после cooldown позволяет в один UTC день получить несколько дневных потерь. Если цель - жесткий daily stop, надо заменить cooldown-reset на manual/new-day reset.

## Archive comparison: old pump vs current orchestrator

Архив `logs_archive/09.05.2026/pump/`:

| Period | Trades | WR | Net | Avg TP | Avg SL | Median hold |
|---|---:|---:|---:|---:|---:|---:|
| Archive all 03-09.05 | 234 | 42.7% | +79.29% | +2.018% | -0.914% | 4m |
| Archive 06-08.05 | 197 | 45.2% | +89.81% | +2.067% | -0.871% | 4m |
| Current all 09-18.05 | 523 | 36.3% | -56.10% | +1.669% | -1.120% | 7m |
| Current 16-18.05 | 121 | 32.2% | -19.98% | +1.606% | -1.007% | 6m |

Вывод по гипотезе "перемудрили с фильтрами":

- Частично подтверждается. Новый confirm-фильтр режет много сигналов, но **не повышает WR** открытых сделок до уровня архива. Значит фильтр строгий, но не селективный по outcome.
- Деградация не только рынок. Archive 06-08 имел намного лучшее payoff ratio: avg TP +2.067% против avg SL -0.871%. Current имеет avg TP +1.669% против avg SL -1.120%. Это изменение exit/payoff структуры.
- Старый engine быстрее закрывал сделки: median hold 4m против 6-7m, и не имел такого MFE->SL хвоста. Новый orchestrator держит позиции дольше без protective trail.
- Path B в новом orchestrator возвращает старый standalone-вход, но без доказанного edge: 3/3 SL на 18.05.

Практический вывод: проблема не в том, что фильтров слишком много вообще, а в том, что текущие фильтры **не отсекают плохие continuation failures**, при этом exits не защищают уже появившуюся прибыль.

## Top-3 practical improvements with numeric impact

### 1. Add breakeven trail after 1R or MFE >= 0.8-1.0%

Expected impact on 16-18.05:

- `MFE_R >= 1.0 -> SL to entry`: **+21.56 п.п.**, net **-19.98% -> +1.58%**.
- `MFE >= 1.0% -> SL to entry`: **+20.36 п.п.**, net **-19.98% -> +0.38%**.
- 18.05 alone: `MFE >= 1.0%` gives **+13.87 п.п.**, **-15.07% -> -1.19%**.

Production recommendation: start with **1R trigger**, not 0.3%, because 0.3% may cut future TP and labels cannot prove sequence safety.

### 2. Disable Path B by default

Expected impact on 16-18.05:

- Removes 3 trades, all SL.
- Net impact: **+3.614 п.п.**.
- 18.05 improves from **-15.07% to -11.45%**.

This is lower impact than breakeven, but high confidence because Path B violates the intended confirmation contract.

### 3. Replace permanent blacklist with pair risk profiles

Expected impact if fully blacklisting worst group on 16-18.05:

- `APR+BSB+BABY+RIVER`: **+22.50 п.п.**, net **-19.98% -> +2.52%**.
- `BSB+BILL+APR+RIVER`: **+20.22 п.п.**, net **-19.98% -> +0.24%**.

But this is overfit on 3 days. Safer implementation:

- `BABY`, `RIVER`: temporary hard ban until more data.
- `APR`, `BSB`: trade only with breakeven enabled, max 1 trade/day or size 0.5.
- `BILL`: no permanent blacklist; apply temporary ban after intraday SL cluster.

Expected conservative impact from dynamic "ban after 2 same-day SL streak": **+4.75 п.п.** on 16-18.05. Less than static blacklist, but lower risk of deleting future winners.

## Recommended implementation order

1. Disable Path B now using the diff above.
2. Implement breakeven trail:
   - trigger: `mfe_r >= 1.0` or `mfe_pct >= 1.0`;
   - new SL: entry price gross, net expectation -fee;
   - log fields: `be_armed_at`, `be_trigger_mfe_pct`, `exit_reason=BE` when hit.
3. Add pair risk overrides in config, not permanent blacklist first:

```yaml
pump_orchestrator:
  pair_risk_overrides:
    BABY-USDT-SWAP:
      size_mult: 0.0
      reason: "0 TP / 5 SL on 2026-05-16..18"
    RIVER-USDT-SWAP:
      size_mult: 0.0
      reason: "0 TP / 3 SL, includes 433m MFE->SL"
    APR-USDT-SWAP:
      size_mult: 0.5
      require_breakeven: true
    BSB-USDT-SWAP:
      size_mult: 0.5
      require_breakeven: true
    BILL-USDT-SWAP:
      max_trades_per_day: 2
      ban_after_sl_streak: 2
```

## Open risks

- Breakeven simulation from labels assumes that a trade with high MFE later hit entry before SL. This is usually true for SL after positive MFE, but exact tick-order simulation should be added before final parameter selection.
- Tape analysis used only 52/121 trades because local tick files are incomplete for many symbols. Current result is enough to reject a naive buy_ratio filter, not enough to finalize a production threshold.
- Archive comparison covers different market dates. The payoff/hold-time deterioration is real in logs, but exact attribution between market regime and code changes needs a controlled replay.
