# -*- coding: utf-8 -*-
"""
main_signal_autopsy_v2.py — РИГОРОЗНАЯ значимость аутопсии майна (поправка на перекрытие). Read-only.

Агент-методолог: пулом-IC на перекрывающихся форвард-доходностях ЗАВЫШАЕТ значимость (ловушка прошлых «+10%»).
Достраиваю честно: (1) бутстрап-CI для IC каждой фичи (исключает ли 0?); (2) hit-rate + биномиальный тест направления
(+ лонг/шорт сплит = base-rate); (3) partial-corr di_dir|adx (shadow ли); (4) бутстрап-CI variance-ratio режимов.
Если CI фич включают 0 и VR-режимов включают 1 → майн видит ШУМ, статзначимости нет.
"""
import sys, io, os, gzip, json, csv
from datetime import datetime
import numpy as np
from scipy.stats import spearmanr, binomtest
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m"); SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
rng=np.random.default_rng(42)

def fnum(x):
    try: return float(x)
    except: return None
def load5(sym,ts_ms):
    rows=[]
    for off in (-1,0):
        d=datetime.utcfromtimestamp(ts_ms/1000+off*86400).strftime("%Y-%m-%d")
        p=os.path.join(F5,sym,d+".csv.gz"); p2=os.path.join(F5,sym,d+".csv")
        fp=p if os.path.exists(p) else (p2 if os.path.exists(p2) else None)
        if not fp: continue
        op=gzip.open if fp.endswith(".gz") else open
        with op(fp,"rt",encoding="utf-8",errors="ignore") as fh: rows+=list(csv.DictReader(fh))
    seen=set();out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]);out.append(r)
    return out

def boot_ic(x,y,B=3000):
    p=[(a,b) for a,b in zip(x,y) if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)]
    if len(p)<20: return None
    X=np.array([a for a,_ in p]); Y=np.array([b for _,b in p]); n=len(p)
    base=spearmanr(X,Y)[0]; bs=[]
    for _ in range(B):
        idx=rng.integers(0,n,n)
        if np.std(X[idx])==0 or np.std(Y[idx])==0: continue
        bs.append(spearmanr(X[idx],Y[idx])[0])
    lo,hi=np.percentile(bs,[2.5,97.5]); return base,lo,hi,n

def partial_corr(f,z,y):
    """corr(f,y | z)."""
    p=[(a,b,c) for a,b,c in zip(f,z,y) if None not in (a,b,c) and all(np.isfinite([a,b,c]))]
    if len(p)<20: return None
    F=np.array([a for a,_,_ in p]); Z=np.array([b for _,b,_ in p]); Y=np.array([c for _,_,c in p])
    def r(a,b): return spearmanr(a,b)[0]
    rfy,rfz,rzy=r(F,Y),r(F,Z),r(Z,Y)
    den=np.sqrt((1-rfz**2)*(1-rzy**2))
    return (rfy-rfz*rzy)/den if den>0 else None, rfy

def main():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    D=[]; paths=[]
    for s in snaps:
        sym=s["symbol"]; sgn=1 if s["side"] in("buy","long") else -1; ts=int(s["ts_ms"])
        H=max(6,int(s.get("hold_min",75))//5); rows=load5(sym,ts)
        if len(rows)<14: continue
        cl=[fnum(r["close"]) for r in rows]
        fire=min(range(len(rows)),key=lambda i:abs(int(rows[i]["ts_ms"])-ts))
        if fire>=len(rows)-3 or not cl[fire]: continue
        j=min(fire+H,len(rows)-1); fwd=sgn*(cl[j]-cl[fire])/cl[fire]*100 if cl[j] else None
        ctx=s.get("context",{}); di=fnum(ctx.get("plus_di_1h")); dm=fnum(ctx.get("minus_di_1h"))
        r5=fnum(rows[fire].get("rsi"))
        D.append({"sgn":sgn,"side":s["side"],"regime":s.get("regime"),"fwd":fwd,
            "vol_ratio":fnum(ctx.get("vol_ratio_sig")),"adx_1h":fnum(ctx.get("adx_1h")),
            "di_dir":(sgn*(di-dm)) if (di is not None and dm is not None) else None,
            "rsi_edge":((50-r5) if sgn>0 else (r5-50)) if r5 is not None else None})
        paths.append((s.get("regime"),[cl[k] for k in range(fire,min(fire+H+1,len(rows))) if cl[k]]))
    n=len(D); fwd=[d["fwd"] for d in D]; print(f"=== РИГОРОЗ-АУТОПСИЯ: {n} сигналов ===\n")

    print("1) IC с БУТСТРАП-CI (95%) — исключает ли 0:")
    print(f"   {'фича':12}{'IC':>7}{'   95% CI':>20}  значимо?")
    for k in ("vol_ratio","adx_1h","di_dir","rsi_edge"):
        r=boot_ic([d[k] for d in D],fwd)
        if r is None: continue
        base,lo,hi,nn=r; sig = (lo>0 or hi<0)
        print(f"   {k:12}{base:>+7.2f}   [{lo:+.2f},{hi:+.2f}]   {'ДА' if sig else 'НЕТ (0 внутри = шум)'}")

    print(f"\n2) НАПРАВЛЕНИЕ (hit-rate + биномиальный тест vs 0.5):")
    hits=[1 if (d['fwd'] is not None and d['fwd']>0) else 0 for d in D if d['fwd'] is not None]
    hr=np.mean(hits); pv=binomtest(sum(hits),len(hits),0.5).pvalue
    print(f"   общий hit-rate {hr*100:.0f}%  (p={pv:.2f}, {'есть навык' if pv<0.05 and hr>0.5 else 'НЕ отличим от монетки'})")
    for sd in ("buy","sell","long","short"):
        h=[1 if d['fwd']>0 else 0 for d in D if d['side']==sd and d['fwd'] is not None]
        if h: print(f"     {sd:6} n={len(h):3} hit {np.mean(h)*100:.0f}% | средн {np.mean([d['fwd'] for d in D if d['side']==sd and d['fwd'] is not None]):+.2f}%")

    print(f"\n3) SHADOW-ТЕСТ (partial corr фичи vs fwd, КОНТРОЛИРУЯ adx_1h):")
    for k in ("di_dir","vol_ratio"):
        pc=partial_corr([d[k] for d in D],[d["adx_1h"] for d in D],fwd)
        if pc: print(f"   {k}: сырая IC {pc[1]:+.2f} → частная|adx {pc[0]:+.2f}  {'(тень adx)' if abs(pc[0])<0.04 else '(своя инфа)'}")

    print(f"\n4) РЕЖИМ — variance-ratio с БУТСТРАП-CI (VR>1 тренд, <1 разворот, CI вкл.1 = арбитражный лейбл):")
    for reg in sorted({p[0] for p in paths}):
        vrs=[]
        for (rg,p) in paths:
            if rg!=reg or len(p)<8: continue
            r=np.diff(np.log(p));
            if r.std()==0: continue
            q=4; m=len(r)//q*q
            if m<q: continue
            vrs.append(np.var(r[:m].reshape(-1,q).sum(axis=1))/(q*np.var(r)))
        if len(vrs)<5: continue
        vrs=np.array(vrs); bs=[np.mean(rng.choice(vrs,len(vrs))) for _ in range(3000)]
        lo,hi=np.percentile(bs,[2.5,97.5])
        verdict = "трендит" if lo>1 else ("разворот" if hi<1 else "АРБИТРАЖНЫЙ (CI вкл.1)")
        print(f"   {str(reg):10} n={len(vrs):3} VR={vrs.mean():.2f} CI[{lo:.2f},{hi:.2f}]  → {verdict}")
    print(f"\n   ВЕРДИКТ математика: если IC-CI включают 0, направление=монетка, di_dir=тень, TRENDING-CI вкл.1 →")
    print(f"   майн гейтит на ШУМЕ и классифицирует режим произвольно. Реальный (слабый) сигнал — только rsi_edge, если CI>0.")

if __name__=="__main__":
    main()
