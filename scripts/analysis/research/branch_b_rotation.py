# -*- coding: utf-8 -*-
"""
branch_b_rotation.py — Branch B: ИНФОРМАЦИЯ/ДАННЫЕ как опережающий сигнал, не цена-паттерн.
Канальная гипотеза (SKLV/MidChart): ETHBTC и сила BTC опережают перформанс альтов; ротация капитала.
Тест на 2-летних дневках OKX (длиннее тейпа = честнее), train/holdout. Read-only.

ЗАФИКСИРОВАННОЕ ПРЕДСКАЗАНИЕ (до запуска):
  Прошлые находки: альты = бета BTC в медведе, cross-sectional ~0. Ожидаю: ETHBTC-моментум имеет
  СЛАБО-положительный IC к форвард-доходности альтов (риск-он прокси), НО ротация-стратегия в OOS
  едва ли робастно бьёт простой BTC-трендфильтр; в медвежьей части альты валятся при любом сигнале.
  Робастного OOS-преимущества ротации над трендом НЕ жду. Если ETHBTC даёт инфу СВЕРХ BTC-тренда в holdout — сюрприз.
"""
import sys, io, os, csv
import numpy as np
from scipy.stats import spearmanr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_okxhist")
ALTS = ["SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "SUI", "APT", "NEAR"]
L = 14            # окно моментума, дней
FWD = 5          # форвард-горизонт для IC

def load(sym):
    p = os.path.join(H, f"{sym}_1D.csv")
    if not os.path.exists(p): return {}
    out = {}
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[int(r["ts"])] = float(r["c"])
    return out

def main():
    btc = load("BTC"); eth = load("ETH")
    alts = {s: load(s) for s in ALTS if load(s)}
    days = sorted(set(btc) & set(eth) & set.intersection(*[set(v) for v in alts.values()]))
    if len(days) < 100:
        print("мало общих дней"); return
    bc = np.array([btc[d] for d in days]); ec = np.array([eth[d] for d in days])
    A = np.array([[alts[s][d] for d in days] for s in alts])      # [n_alts, T]
    T = len(days)
    altret = np.zeros(T)                                          # дневная доходность равновес. корзины
    altret[1:] = np.mean((A[:, 1:] / A[:, :-1] - 1), axis=0)
    ratio = ec / bc
    def mom(x, i):
        return x[i] / x[i - L] - 1 if i >= L else np.nan
    # сигналы и форвард
    eb_mom = np.array([mom(ratio, i) for i in range(T)])
    btc_mom = np.array([mom(bc, i) for i in range(T)])
    alt_mom = np.array([np.prod(1 + altret[i - L + 1:i + 1]) - 1 if i >= L else np.nan for i in range(T)])
    fwd = np.array([np.prod(1 + altret[i + 1:i + 1 + FWD]) - 1 if i + FWD < T else np.nan for i in range(T)])

    print(f"=== BRANCH B: ИНФО-СИГНАЛЫ → форвард альт-корзины (T={T} дн, {len(alts)} альтов, L={L}) ===\n")
    from datetime import datetime
    split = int(T * 0.6)
    print(f"период: {datetime.utcfromtimestamp(days[0]/1000):%Y-%m-%d}..{datetime.utcfromtimestamp(days[-1]/1000):%Y-%m-%d}  | train<{datetime.utcfromtimestamp(days[split]/1000):%Y-%m-%d}, holdout после\n")

    def ic(sig, lo, hi, name):
        m = np.isfinite(sig[lo:hi]) & np.isfinite(fwd[lo:hi])
        x, y = sig[lo:hi][m], fwd[lo:hi][m]
        if len(x) < 20: print(f"  {name:22} мало"); return
        r = spearmanr(x, y)[0]; t = r * np.sqrt((len(x) - 2) / (1 - r**2 + 1e-12))
        print(f"  {name:22} IC={r:+.3f} t={t:+.1f} {'ЗНАЧИМ' if abs(t) > 2 else 'шум'} (n={len(x)})")

    print("IC сигнала к forward-5 альт-корзины (TRAIN / HOLDOUT):")
    for nm, s in (("ETHBTC-моментум", eb_mom), ("BTC-моментум", btc_mom), ("альт-корзина-моментум", alt_mom)):
        print(f"  -- {nm}")
        ic(s, L, split, "   train"); ic(s, split, T - FWD, "   holdout")

    # стратегии: лонг корзины при сигнале>0, иначе кэш; дневной ребаланс, кост 0.05% на переключение
    def run(sigmask, lo, hi):
        pos = (sigmask > 0).astype(float)
        rets = []
        for i in range(lo, hi):
            if not np.isfinite(pos[i]): continue
            r = pos[i] * altret[i + 1] if i + 1 < T else 0
            if i > lo and pos[i] != pos[i - 1]: r -= 0.0005
            rets.append(r)
        rets = np.array(rets)
        if len(rets) < 10: return None
        sh = rets.mean() / (rets.std() + 1e-12) * np.sqrt(365)
        eq = np.cumprod(1 + rets); dd = np.min(eq / np.maximum.accumulate(eq) - 1)
        return sh, (eq[-1] - 1) * 100, dd * 100, 100 * np.mean(pos[lo:hi] > 0)

    print(f"\nСтратегии (лонг корзины при сигнале>0, иначе кэш), TRAIN / HOLDOUT [Sharpe | total% | maxDD% | в рынке%]:")
    strat = {"ETHBTC>0": eb_mom, "BTC-trend>0": btc_mom, "ETHBTC&BTC>0": np.where((eb_mom > 0) & (btc_mom > 0), 1.0, -1.0),
             "altmom>0": alt_mom}
    for nm, s in strat.items():
        for lbl, lo, hi in (("train", L, split), ("holdout", split, T - 1)):
            r = run(s, lo, hi)
            if r: print(f"  {nm:14} {lbl:8} Sharpe={r[0]:+.2f}  total={r[1]:+.1f}%  maxDD={r[2]:.0f}%  expo={r[3]:.0f}%")
    # бенчмарк buy&hold корзины
    for lbl, lo, hi in (("train", L, split), ("holdout", split, T - 1)):
        rets = altret[lo + 1:hi + 1]
        sh = rets.mean() / (rets.std() + 1e-12) * np.sqrt(365)
        eq = np.cumprod(1 + rets); dd = np.min(eq / np.maximum.accumulate(eq) - 1)
        print(f"  {'BUY&HOLD':14} {lbl:8} Sharpe={sh:+.2f}  total={(eq[-1]-1)*100:+.1f}%  maxDD={dd*100:.0f}%  expo=100%")

    print(f"\nВЕРДИКТ: ETHBTC живой ТОЛЬКО если в HOLDOUT (1) его IC значим И (2) ротация бьёт BUY&HOLD и BTC-trend по Sharpe/DD.")

if __name__ == "__main__":
    main()
