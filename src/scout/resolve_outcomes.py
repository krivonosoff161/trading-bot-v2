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
import sys
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.scanner_journal import JOURNAL, OUT_DIR, now_iso  # noqa: E402

OUTCOMES = OUT_DIR / "scanner_outcomes.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 resolve-outcomes; keyless)"}
TIMEOUT = 20

# --- провизорные пороги скоринга (V1 → config.yaml, см. SCANNER_SPEC лок) ---
NO_GO_FLAT_PCT = 3.0     # NO_GO «правильно», если |ret| < этого (большого хода не упустили)
GO_MIN_EXCESS = 0.0      # GO «правильно», если excess по стороне положителен


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


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


def score(verdict: str, side: str, asset_ret: float, excess: float) -> bool | None:
    """Правильна ли карточка по форварду. None = не определяемо автоматически."""
    if verdict == "GO":
        if side == "long":
            return excess > GO_MIN_EXCESS and asset_ret > 0
        if side == "short":
            return excess < -GO_MIN_EXCESS and asset_ret < 0
        return None
    if verdict == "NO_GO":
        # фильтр «не лезь» правильный, если большого хода (в любую сторону) не было
        return abs(asset_ret) < NO_GO_FLAT_PCT
    return None  # WATCH = информационный, не скорим бинарно


def resolve() -> None:
    rows = _read_jsonl(JOURNAL)
    if not rows:
        print("журнал пуст — нечего считать")
        return
    done = _scored_ids()
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

            inst = r.get("okx_inst")
            p0 = r.get("price_at_decision")
            btc0 = r.get("btc_at_decision")
            if r.get("outcome_source") == "manual" or not inst or p0 in (None, 0):
                rec = {"card_id": cid, "resolved_ts": now_iso(), "scored": False,
                       "note": "manual — актив вне OKX / нет цены входа, исход дописывает трейдер",
                       "verdict": r.get("verdict"), "asset": r.get("asset")}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                manual += 1
                continue

            p_now = okx_last(inst)
            if p_now is None:
                pending += 1
                continue
            asset_ret = (p_now / p0 - 1.0) * 100.0
            bsym = r.get("baseline_symbol") or "BTC-USDT-SWAP"   # per-layer якорь (не хардкод BTC)
            if bsym not in baseline_cache:
                baseline_cache[bsym] = okx_last(bsym)
            b_now = baseline_cache[bsym]
            baseline = ((b_now / btc0 - 1.0) * 100.0) if (b_now and btc0) else None
            excess = (asset_ret - baseline) if baseline is not None else None
            correct = score(r.get("verdict", ""), r.get("side", "none"),
                            asset_ret, excess if excess is not None else asset_ret)

            rec = {
                "card_id": cid, "resolved_ts": now_iso(), "scored": True,
                "verdict": r.get("verdict"), "side": r.get("side"), "asset": r.get("asset"),
                "price_at_decision": p0, "price_now": p_now,
                "asset_ret_pct": round(asset_ret, 3),
                "baseline_ret_pct": round(baseline, 3) if baseline is not None else None,
                "excess_pct": round(excess, 3) if excess is not None else None,
                "verdict_correct": correct,
                "horizon_hours": r.get("horizon_hours"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            scored += 1
            mark = {True: "✓", False: "✗", None: "?"}[correct]
            ex = f"{excess:+.2f}%" if excess is not None else "n/a"
            print(f"  [{mark}] {r.get('verdict')} {r.get('side')} {r.get('asset')}: "
                  f"ret={asset_ret:+.2f}% baseline={baseline if baseline is None else round(baseline,2)}% excess={ex}")

    print(f"\nзрело={matured} · посчитано={scored} · manual-очередь={manual} · ещё рано={pending}")
    print(f"outcomes: {OUTCOMES}")


def report() -> None:
    """Сводка: WR/excess по вердиктам (соединяет журнал и outcomes по card_id)."""
    jrows = {r["card_id"]: r for r in _read_jsonl(JOURNAL)}
    orows = _read_jsonl(OUTCOMES)
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
    print("\nвердикт   n   correct%   mean_excess%")
    for v, lst in sorted(by_verdict.items()):
        n = len(lst)
        cor = [o for o in lst if o.get("verdict_correct") is True]
        exs = [o["excess_pct"] for o in lst if o.get("excess_pct") is not None]
        wr = f"{100*len(cor)/n:.0f}%" if n else "—"
        me = f"{sum(exs)/len(exs):+.2f}" if exs else "—"
        print(f"  {v:<8} {n:<3} {wr:<9} {me}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="только сводка, без счёта")
    args = ap.parse_args()
    if args.report:
        report()
    else:
        resolve()
        print()
        report()


if __name__ == "__main__":
    main()
