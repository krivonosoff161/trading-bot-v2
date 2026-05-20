# Postmortem — Pump Reversal Scalp (ws_smart_pump)

**Статус:** ЗАКРЫТО 20.05.2026 — отрицательный результат (fee-blocked).
**Файл движка:** `scripts/ws/ws_smart_pump.py` (live paper, остановлен 20.05).
**Не реанимировать без нового research.**

---

## Что тестировалось

Реверсальный скальп на alt-coin фьючерсах OKX: после взрывной 1m свечи
(`|price_change| >= 0.8%`, `vol_ratio >= 2.0`) вход **против** направления взрыва.
Гипотеза: 59% времени после взрыва — откат (research 19.05).

Eligible pairs по reversal WR ≥ 53%: BILL (n=895), JELLYJELLY (n=36), NOT (n=19).
Параметры: `sl_pct=0.8`, `tp_pct=1.5`, `max_hold=15m`, BE-промоушн (MFE≥0.5%→BE, ≥0.7%→lock+0.2%).

## Итерации

1. Reversal universe scan (46→49 пар) — 13-16 eligible, пар-специфичный edge.
2. Intra-candle early entry (confirm=0, триггер 0.5% за ≤20с).
3. Live paper: 37 сделок — TP 8%, BE-exit 78%, реальные SL 8%.
4. Param sweep BILL (20.05): 320 комбинаций SL/TP/hold/BE.
5. Network context (parent_network через CoinGecko): aligned/opposite vs BTC/SOL.
6. MFE-распределение + гипотезы H1-H8.

## Почему закрыто

**Комиссионный барьер — структурный, не параметрический.**

- Гросс reversal-edge мал: BILL avg_return 0.04-0.18%, WR ~53-54%.
- Комиссия **0.20% RT (тейкер)** перекрывает edge. Лимитка/мейкер невозможна — на взрывных
  низколиквидных альтах вход берёт ликвидность (подтверждено практикой трейдера).
- **Param sweep: 0 из 320 комбинаций net-положительны** на надёжных sample (BILL n=983, UB n=389).
  Лучшая (`SL 1.2 / TP 0.7 / 5m / no BE`) = −0.16%, текущий прод-конфиг = −0.21%.
- Network context не подтвердился: aligned/opposite бакеты n=0-6, вердикт «no filter».
- Единственные «плюсовые» пары (NOT +0.36%, JELLYJELLY +0.12%) — на n=19-36, шум.

Reversal был ответом на провал momentum-движка (`ws_pump_orchestrator`: n=560, WR=34.6%,
net=−74%). Теперь **оба направления pump-скальпа при тейкер-комиссии на этих альтах убыточны**.

## Lessons learned

- **WR > 50% ≠ прибыль.** Гросс-edge обязан превышать 0.20% RT, иначе positive WR ничего не значит.
- **На мелких быстрых движениях комиссия — главный барьер.** Нужен размер хода >> комиссии:
  редкие крупные движения бьют fee, частый мелкий скальп — нет.
- **Лимитка не спасает** там где вход берёт ликвидность (slippage). Тейкер — данность.
- **Кластер взрывов (2+ за 5м) углубляет MFE** (2.54% vs 1.85%) — полезный режим-фильтр на будущее.
- **Ранний BE убивает edge:** в live BE-exit 78% — стоп в ноль при списанной комиссии.

## Что переиспользуется

- Тиковый загрузчик + детектор событий (`continuation_research_20_05_2026.py`).
- MFE / param-sweep harness, методика eligible-universe scan.
- Risk-containment паттерны (`session_ban_sl_no_tp`, `pair_risk_overrides`) — как референс.
- **Пивот: continuation вместо reversal** — реальный edge трейдера в движении ПО направлению
  серии, не против. См. `docs/gpt_continuation_research_v2_20_05_2026.md`.

## Связанные документы

- `scripts/analysis/research/output/continuation_*_20_05_2026.md` — research, доказавший fee-block
- `docs/gpt_continuation_research_v2_20_05_2026.md` — новое направление (round 2)
