# -*- coding: utf-8 -*-
"""
short_horizon_mr.py — ГИПОТЕЗА РЕШЕНИЯ: фичи майна — контртрендовые на коротком горизонте. Read-only.

Аутопсия: vol_ratio IC −0.22 на 3 барах (контртренд!). Майн юзает их как моментум → знак перевёрнут.
Тут — РИГОРОЗНО на ВСЕЙ вселенной (57 монет × тысячи баров → n огромный, значимость реальна):
  (1) signed-IC z-score/rsi/vol-spike vs forward 3/6 баров — есть ли значимый контртренд-сигнал;
  (2) fade-стратегия (шорт перекупленность/всплеск, лонг перепроданность, hold 3) — NET мейкер vs тейкер;
  train(11-18)/holdout(19-26). Если IC значим И стратегия + на мейкере OOS → вот эдж, что майн misuses.
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
import numpy as np
from scipy.stats import spearmanr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
if not os.path.isdir(F5) or not os.listdir(F5):          # после сброса 27.05 фичи переехали в архив
    F5=os.path.join(ROOT,"logs_archive","pre-reset_2026-05-27","features","5m")
TRAIN_END="2026-05-18"
rng=np.random.default_rng(7)

def fnum(x):
    try: return float(x)
    except: return None
def load_all(sym):
    d=datetime(2026,5,11); e=datetime(2026,5,26); rows=[]
    base=os.path.join(F5,sym)
    if not os.path.isdir(base): return []
    while d<=e:
        for ext,op in ((".csv.gz",gzip.open),(".csv",open)):
            p=os.path.join(base,d.strftime("%Y-%m-%d")+ext)
            if os.path.exists(p):
                with op(p,"rt",encoding="utf-8",errors="ignore") as fh: rows+=list(csv.DictReader(fh)); break
        d+=timedelta(days=1)
    seen=set();out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]);out.append(r)
    return out

def main():
    syms=sorted(os.listdir(F5))
    Z=[];RS=[];VS=[];F3=[];F6=[];DAY=[]
    trades=[]   # (day, z, fwd1..ret per bar) для fade-стратегии
    for s in syms:
        rows=load_all(s)
        if len(rows)<60: continue
        c=[fnum(r["close"]) for r in rows]; rsi=[fnum(r.get("rsi")) for r in rows]
        hi=[fnum(r["high"]) for r in rows]; lo=[fnum(r["low"]) for r in rows]
        for i in range(20,len(rows)-7):
            if None in (c[i],c[i-20]): continue
            w=[c[j] for j in range(i-20,i) if c[j]]
            if len(w)<20: continue
            m=np.mean(w); sd=np.std(w) or 1e-9
            z=(c[i]-m)/sd
            rng_=[(hi[j]-lo[j]) for j in range(i-20,i) if hi[j] and lo[j]]
            vs=(hi[i]-lo[i])/(np.mean(rng_) or 1e-9) if (hi[i] and lo[i]) else None
            f3=(c[i+3]-c[i])/c[i]*100 if c[i+3] else None
            f6=(c[i+6]-c[i])/c[i]*100 if c[i+6] else None
            day=datetime.utcfromtimestamp(int(rows[i]["ts_ms"])/1000).strftime("%Y-%m-%d")
            if None in (z,f3,f6): continue
            Z.append(z); RS.append(rsi[i]); VS.append(vs); F3.append(f3); F6.append(f6); DAY.append(day)
            trades.append((day,z,c[i],c[i+3]))
    n=len(Z); print(f"=== КОНТРТРЕНД НА ВСЕЙ ВСЕЛЕННОЙ: {n} баро-точек, {len(syms)} монет ===\n")
    Z=np.array(Z);F3=np.array(F3);F6=np.array(F6)
    def ic_sig(x,y,name):
        m=np.isfinite(x)&np.isfinite(y); x,y=x[m],y[m]
        base=spearmanr(x,y)[0]
        t=base*np.sqrt((len(x)-2)/(1-base**2+1e-12))   # аналитический t (n огромный)
        print(f"  {name:18} IC={base:+.3f}  t={t:+.1f}  {'ЗНАЧИМ' if abs(t)>3 else 'шум'}  (n={len(x)})")
        return base
    print("1) signed-IC (z-score цены vs будущий ход — отрицательный = mean-reversion):")
    ic_sig(Z,F3,"z vs fwd3"); ic_sig(Z,F6,"z vs fwd6")
    RSa=np.array([r if r is not None else np.nan for r in RS])
    ic_sig(RSa-50,F3,"(rsi-50) vs fwd3")
    VSa=np.array([v if v is not None else np.nan for v in VS])
    ic_sig(VSa,F3,"vol-spike vs fwd3")
    print(f"\n2) FADE по ЭКСТРЕМАЛЬНОСТИ: растёт ли gross-эдж на хвостах |z| (HOLDOUT, hold 3):")
    print(f"   {'порог |z|':>10}{'GROSS':>9}{'мейкер-NET':>12}{'тейкер-NET':>12}{'WR':>6}{'сделок':>8}")
    for thr in (2,3,4,5,6):
        g=[]
        for day,z,e,x in trades:
            if day<=TRAIN_END or abs(z)<thr or not e or not x: continue
            sgn=-1 if z>0 else 1
            g.append(sgn*(x-e)/e*100)
        if len(g)<20: print(f"   {thr:>10}  мало сделок (n={len(g)})"); continue
        g=np.array(g); wr=100*np.mean(g>0)
        print(f"   {thr:>10}{g.mean():>+9.3f}%{g.mean()-0.02:>+11.3f}%{g.mean()-0.10:>+11.3f}%{wr:>5.0f}%{len(g):>8}")
    print(f"\n   Карман торгуем, если GROSS растёт с |z| И мейкер-NET становится + на хвостах (редко, но резко).")
    print(f"   Если gross не растёт — MR-эдж равномерно ниже косты = структурно не достаётся ритейлом.")

if __name__=="__main__":
    main()
