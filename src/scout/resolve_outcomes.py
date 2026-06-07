# -*- coding: utf-8 -*-
"""
resolve_outcomes.py — форвард-счёт карточек сканера с baseline/excess.

ОТДЕЛЬНО от label_main_ws (тот — прод-измеритель Main, не трогаем). Закрывает
главный методологический риск (грабли прошлого research): «+2% на GO» ничего не
значит, если рынок сам +2% (бета). Поэтому считаем EXCESS = ret_актива − ret_BTC
за тот же горизонт, и метрика журнала = excess по вердиктам, не сырой ret.

Поток: читает scanner_journal.jsonl → для карточек, у которых горизонт ИСТЁК и
исход ещё не посчитан → берёт текущую цену актива и BTC (OKX keyless) → считает
ret/baseline/excess → скорит по вердикту → пишет в scanner_outcomes.jsonl по card_id.

Активы вне OKX (okx_inst=None / outcome_source=manual) → помечаются для ручного
дописывания трейдером (не скорятся автоматически).

Запуск:  python src/scout/resolve_outcomes.py            # счёт зрелых карточек
         python src/scout/resolve_outcomes.py --report   # только сводка по журналу
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.scanner_journal import JOURNAL, OUT_DIR, now_iso  # noqa: E402
from src.scout import scanner_records as R  # noqa: E402
from src.scout.router import baseline_for_layer, tracked_assets  # noqa: E402

OUTCOMES = OUT_DIR / "scanner_outcomes.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 resolve-outcomes; keyless)"}
TIMEOUT = 20

# --- провизорные пороги скоринга (V1 → config.yaml, см. SCANNER_SPEC лок) ---
NO_GO_FLAT_PCT = 3.0     # NO_GO «правильно», если |ret| < этого (большого хода не упустили)
REPORT_THRESHOLDS = (2.0, 3.0, 5.0)
GO_MIN_EXCESS = 0.0      # GO «правильно», если excess по стороне положителен


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _asset_ref(asset: str | None) -> dict:
    sym = str(asset or "").upper()
    for row in tracked_assets():
        if str(row.get("sym") or "").upper() == sym:
            return row
    return {}


def okx_last(inst_id: str | None) -> float | None:
    if not inst_id:
        return None
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
                         params={"instId": inst_id}, headers=UA, timeout=TIMEOUT)
        d = r.json()
        if str(d.get("code")) == "0" and d.get("data"):
            return float(d["data"][0]["last"])
    except Exception:
        return None
    return None


def fetch_path(inst_id: str, t0_ms: int, t_end_ms: int, bar: str = "1H") -> list:
    """Свечи в окне [t0, t_end] для price-path (mfe/mae/точки). [(ts,high,low,close),…] или []."""
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
                         params={"instId": inst_id, "bar": bar, "limit": "300"}, headers=UA, timeout=TIMEOUT)
        d = r.json()
        if str(d.get("code")) != "0":
            return []
        out = [(int(c[0]), float(c[2]), float(c[3]), float(c[4]))
               for c in (d.get("data") or []) if t0_ms <= int(c[0]) <= t_end_ms]
        out.sort()
        return out
    except Exception:
        return []


def _price_at(path: list, target_ms: int) -> float | None:
    """Цена (close) ближайшей свечи к target_ms (в пределах окна)."""
    if not path:
        return None
    best = min(path, key=lambda c: abs(c[0] - target_ms))
    return best[3]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def _scored_ids() -> set[str]:
    return {r.get("card_id") for r in _read_jsonl(OUTCOMES)}


def _latest_by_card(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for row in rows:
        cid = row.get("card_id")
        if cid:
            out[cid] = row
    return out


def _backup_and_remove(path: Path) -> None:
    if not path.exists():
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(path.suffix + f".bak-{stamp}")
    shutil.copy2(path, backup)
    path.unlink()


def score(verdict: str, side: str, ret: float, mfe_long: float | None, mae_long: float | None) -> bool | None:
    """Правильна ли карточка (обе стороны + амплитуда хода). None = не скорим бинарно."""
    if verdict == "GO":
        if side == "long":
            return ret > 0
        if side == "short":
            return ret < 0
        return None
    if verdict == "NO_GO":
        # «не лезь» правильно, если НЕ упустили крупный ход НИ В ОДНУ сторону (по mfe/mae)
        big = max(abs(mfe_long or 0.0), abs(mae_long or 0.0))
        return big < NO_GO_FLAT_PCT
    return None  # WATCH = информационный


def resolve() -> None:
    rows = _read_jsonl(JOURNAL)
    if not rows:
        print("журнал пуст — нечего считать")
        return
    done = _scored_ids()
    event_index = R.read_index(R.EVENTS)
    reasoning_index = R.read_index(R.REASONING)
    now = dt.datetime.now(dt.timezone.utc)
    baseline_cache: dict = {}      # per-layer якорь excess (кэш цен)

    matured = scored = manual = pending = 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTCOMES, "a", encoding="utf-8") as out:
        for r in rows:
            cid = r.get("card_id")
            if cid in done:
                continue
            try:
                mature_at = _parse(r["ts_utc"]) + dt.timedelta(hours=float(r["horizon_hours"]))
            except Exception:
                continue
            if now < mature_at:
                pending += 1
                continue
            matured += 1

            t0 = _parse(r["ts_utc"])
            t0_ms = int(t0.timestamp() * 1000)
            t_end_ms = int(min(now, t0 + dt.timedelta(hours=float(r["horizon_hours"]))).timestamp() * 1000)

            aref = _asset_ref(r.get("asset"))
            inst = r.get("okx_inst") or aref.get("okx_inst")
            bsym = r.get("baseline_symbol") or aref.get("baseline") or baseline_for_layer(int(r.get("layer") or 0))
            p0 = r.get("price_at_decision")
            btc0 = r.get("btc_at_decision")

            path = fetch_path(inst, max(0, t0_ms - 3600000), t_end_ms) if inst else []
            if p0 in (None, 0) and path:
                p0 = _price_at(path, t0_ms)
            if btc0 in (None, 0) and bsym:
                baseline_path = fetch_path(bsym, max(0, t0_ms - 3600000), t_end_ms)
                btc0 = _price_at(baseline_path, t0_ms)

            if not inst or p0 in (None, 0):
                rec = {"card_id": cid, "resolved_ts": now_iso(), "scored": False,
                       "note": "manual — актив вне OKX / нет цены входа, исход дописывает трейдер",
                       "verdict": r.get("verdict"), "asset": r.get("asset")}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                manual += 1
                continue

            path = [c for c in path if t0_ms <= c[0] <= t_end_ms] or fetch_path(inst, t0_ms, t_end_ms)
            if path:
                price_final = path[-1][3]
                mfe_long = (max(c[1] for c in path) / p0 - 1.0) * 100.0   # макс ВВЕРХ (в пользу лонга)
                mae_long = (min(c[2] for c in path) / p0 - 1.0) * 100.0   # макс ВНИЗ (в пользу шорта)
                price_after = {f"{h}h": (round((v / p0 - 1.0) * 100.0, 3) if v else None)
                               for h in (1, 4, 24, 48)
                               for v in [_price_at(path, t0_ms + h * 3600000)]}
            else:
                p_now = okx_last(inst)
                if p_now is None:
                    pending += 1
                    continue
                price_final, mfe_long, mae_long, price_after = p_now, None, None, {}

            ret = (price_final / p0 - 1.0) * 100.0
            outcome_long, outcome_short = round(ret, 3), round(-ret, 3)   # P&L каждой стороны

            bsym = bsym or "BTC-USDT-SWAP"
            bkey = (bsym, t0_ms, t_end_ms)   # cache per (symbol, window), НЕ только символ — иначе чужое окно
            if bkey not in baseline_cache:
                baseline_cache[bkey] = fetch_path(bsym, t0_ms, t_end_ms)
            baseline_path = baseline_cache[bkey]
            b_final = baseline_path[-1][3] if baseline_path else okx_last(bsym)
            baseline = ((b_final / btc0 - 1.0) * 100.0) if (b_final and btc0) else None
            excess = (ret - baseline) if baseline is not None else None

            correct = score(r.get("verdict", ""), r.get("side", "none"), ret, mfe_long, mae_long)
            big = max(abs(mfe_long or 0.0), abs(mae_long or 0.0))
            volatility_missed = (r.get("verdict") == "NO_GO" and big >= NO_GO_FLAT_PCT)
            directional_missed = (r.get("verdict") == "NO_GO" and abs(ret) >= NO_GO_FLAT_PCT)
            missed = volatility_missed   # backward-compatible field

            rec = {
                "card_id": cid, "resolved_ts": now_iso(), "scored": True,
                "verdict": r.get("verdict"), "side": r.get("side"), "asset": r.get("asset"),
                "price_at_decision": p0, "price_final": round(price_final, 6),
                "ret_pct": round(ret, 3), "outcome_long_pct": outcome_long, "outcome_short_pct": outcome_short,
                "mfe_long_pct": round(mfe_long, 3) if mfe_long is not None else None,
                "mae_long_pct": round(mae_long, 3) if mae_long is not None else None,
                "price_after": price_after,
                "baseline_ret_pct": round(baseline, 3) if baseline is not None else None,
                "excess_pct": round(excess, 3) if excess is not None else None,
                "verdict_correct": correct, "missed_move": missed,
                "volatility_missed_move": volatility_missed,
                "directional_missed_move": directional_missed,
                "horizon_hours": r.get("horizon_hours"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            training = R.build_training_record(
                r,
                rec,
                event_block=event_index.get(cid),
                reasoning_block=reasoning_index.get(cid),
            )
            memory = R.build_memory_record(r, rec, reasoning_block=reasoning_index.get(cid))
            if training:
                R.write_training_record(training)
            if memory:
                R.write_memory_record(memory)
            scored += 1
            mark = {True: "OK", False: "BAD", None: "?"}[correct]
            sw = (f"mfe+{mfe_long:.1f}/mae{mae_long:.1f}" if mfe_long is not None else "путь?")
            print(f"  [{mark}] {r.get('verdict')} {r.get('side')} {r.get('asset')}: "
                  f"ret={ret:+.2f}% long={outcome_long:+.1f}/short={outcome_short:+.1f} {sw}"
                  f"{' MISSED' if missed else ''}")

    print(f"\nзрело={matured} · посчитано={scored} · manual-очередь={manual} · ещё рано={pending}")
    print(f"outcomes: {OUTCOMES}")


def report() -> None:
    """Сводка: WR/excess по вердиктам (соединяет журнал и outcomes по card_id)."""
    jrows = {r["card_id"]: r for r in _read_jsonl(JOURNAL)}
    orows = list(_latest_by_card(_read_jsonl(OUTCOMES)).values())
    print(f"карточек в журнале: {len(jrows)} · посчитано исходов: {len(orows)}")
    by_verdict: dict[str, list] = {}
    for o in orows:
        if not o.get("scored"):
            continue
        v = o.get("verdict", "?")
        by_verdict.setdefault(v, []).append(o)
    if not by_verdict:
        print("посчитанных исходов пока нет (карточки не дозрели или журнал пуст)")
        return
    def _avg(xs):
        return f"{sum(xs)/len(xs):+.2f}" if xs else "—"

    print("\nвердикт   n   correct%  long%   short%  excess%  missed")
    for v, lst in sorted(by_verdict.items()):
        n = len(lst)
        cor = [o for o in lst if o.get("verdict_correct") is True]
        wr = f"{100*len(cor)/n:.0f}%" if n else "—"
        lo = _avg([o["outcome_long_pct"] for o in lst if o.get("outcome_long_pct") is not None])
        sh = _avg([o["outcome_short_pct"] for o in lst if o.get("outcome_short_pct") is not None])
        ex = _avg([o["excess_pct"] for o in lst if o.get("excess_pct") is not None])
        miss = sum(1 for o in lst if o.get("missed_move"))
        print(f"  {v:<8} {n:<3} {wr:<8} {lo:<7} {sh:<7} {ex:<8} {miss}")

    print("\nNO_GO thresholds: directional=|final ret|, volatility=max(|MFE|,|MAE|)")
    no_go = [o for o in orows if o.get("scored") and o.get("verdict") == "NO_GO"]
    for th in REPORT_THRESHOLDS:
        directional = sum(1 for o in no_go if abs(float(o.get("ret_pct") or 0.0)) >= th)
        volatility = sum(
            1 for o in no_go
            if max(abs(float(o.get("mfe_long_pct") or 0.0)), abs(float(o.get("mae_long_pct") or 0.0))) >= th
        )
        n = len(no_go)
        d_rate = f"{100 * directional / n:.0f}%" if n else "—"
        v_rate = f"{100 * volatility / n:.0f}%" if n else "—"
        print(f"  {th:.0f}%: directional {directional}/{n} ({d_rate}) · volatility {volatility}/{n} ({v_rate})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="только сводка, без счёта")
    ap.add_argument("--rebuild", action="store_true", help="backup and rebuild generated outcome/training/memory logs")
    args = ap.parse_args()
    if args.report:
        report()
    else:
        if args.rebuild:
            for path in (OUTCOMES, R.TRAINING, R.MEMORY):
                _backup_and_remove(path)
        resolve()
        print()
        report()


if __name__ == "__main__":
    main()
