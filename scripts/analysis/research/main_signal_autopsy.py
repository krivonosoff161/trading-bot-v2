# -*- coding: utf-8 -*-
"""
main_signal_autopsy.py — МАТЕМАТИЧЕСКАЯ аутопсия сигнала майна: что не так с «видением рынка». Read-only.

Математик-трейдер: разложить сигнал майна на информационное содержание.
  1) IC (Spearman фичи vs будущий направленный ход) — какие фичи майна несут инфу, какие = шум.
  2) IC-decay по горизонтам — совпадает ли горизонт сигнала с холдом.
  3) Редандантность (shadow-фичи) — корреляц. матрица.
  4) Direction vs Timing: hit-rate направления vs capture-ratio (realized/MFE).
  5) Валидность режима: variance-ratio форвард-пути по regime (TRENDING должен VR>1, RANGING VR<1).
Данные: signal_snapshot (контекст майна) + 5m фичи + labels.
"""
import sys, io, os, gzip, json, csv
from datetime import datetime
import numpy as np
from scipy.stats import spearmanr
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m"); SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")

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

def ic(x,y):
    p=[(a,b) for a,b in zip(x,y) if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)]
    if len(p)<15: return None,0
    r,_=spearmanr([a for a,_ in p],[b for _,b in p]); return r,len(p)

def main():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    D=[]; paths=[]
    for s in snaps:
        sym=s["symbol"]; sgn=1 if s["side"] in("buy","long") else -1; ts=int(s["ts_ms"])
        H=max(6,int(s.get("hold_min",75))//5); rows=load5(sym,ts)
        if len(rows)<14: continue
        cl=[fnum(r["close"]) for r in rows]; hi=[fnum(r["high"]) for r in rows]; lo=[fnum(r["low"]) for r in rows]
        fire=min(range(len(rows)),key=lambda i:abs(int(rows[i]["ts_ms"])-ts))
        if fire>=len(rows)-3 or not cl[fire]: continue
        def fret(h):
            j=min(fire+h,len(rows)-1); return sgn*(cl[j]-cl[fire])/cl[fire]*100 if cl[j] else None
        mfe=0.0
        for j in range(fire+1,min(fire+H+1,len(rows))):
            ext=hi[j] if sgn>0 else lo[j]
            if ext and cl[fire]: mfe=max(mfe,sgn*(ext-cl[fire])/cl[fire]*100)
        ctx=s.get("context",{}); ind=s.get("indicators",{})
        di=fnum(ctx.get("plus_di_1h")); dm=fnum(ctx.get("minus_di_1h"))
        e=fnum(s.get("entry")); e15=fnum((ind.get("15m") or {}).get("ema20"))
        r5=fnum(rows[fire].get("rsi")); sl=fnum(rows[fire].get("slope"))
        D.append({"sgn":sgn,"regime":s.get("regime"),"H":H,"fwd":fret(H),"mfe":mfe,
            "fwd3":fret(3),"fwd6":fret(6),"fwd12":fret(12),"fwd18":fret(18),
            "vol_ratio":fnum(ctx.get("vol_ratio_sig")),"adx_1h":fnum(ctx.get("adx_1h")),
            "adx_rising":1.0 if ctx.get("adx_1h_rising") is True else 0.0,
            "day_pos":fnum(ctx.get("day_position")),
            "di_dir":(sgn*(di-dm)) if (di is not None and dm is not None) else None,
            "rsi_edge":((50-r5) if sgn>0 else (r5-50)) if r5 is not None else None,
            "dist_ema":(-sgn*(e/e15-1)*100) if (e and e15) else None,
            "slope_dir":(sgn*sl) if sl is not None else None,
            "bb_exp":1.0 if ctx.get("bb_expanding") is True else 0.0})
        paths.append((sgn,[cl[j] for j in range(fire,min(fire+H+1,len(rows))) if cl[j]]))
    n=len(D); print(f"=== АУТОПСИЯ СИГНАЛА МАЙНА: {n} сигналов ===\n")
    fwd=[d["fwd"] for d in D]; mfe=[d["mfe"] for d in D]
    hit=100*sum(1 for f in fwd if f and f>0)/n
    movers=100*sum(1 for m in mfe if m>=1.0)/n
    cap=np.mean([d["fwd"] for d in D if d["fwd"] is not None])/ (np.mean(mfe) or 1e-9)
    print(f"1) DIRECTION vs TIMING:")
    print(f"   hit-rate направления (fwd>0): {hit:.0f}%  |  имели ход MFE≥1%: {movers:.0f}%  → НАПРАВЛЕНИЕ {'ок' if movers>60 else 'слабое'}")
    print(f"   средний realized {np.mean([f for f in fwd if f is not None]):+.2f}% vs средний MFE {np.mean(mfe):+.2f}%  → CAPTURE-RATIO {cap:.2f}")
    print(f"   (capture<<1 = направление есть, берём малую долю = болезнь ТАЙМИНГА/ВЫХОДА, не отбора)\n")
    feats=["vol_ratio","adx_1h","adx_rising","day_pos","di_dir","rsi_edge","dist_ema","slope_dir","bb_exp"]
    print(f"2) IC каждой фичи майна (Spearman vs forward H-ход):")
    print(f"   {'фича':12}{'IC':>8}{'n':>6}  вердикт")
    ics={}
    for k in feats:
        r,nn=ic([d[k] for d in D],fwd); ics[k]=r
        if r is None: continue
        v="несёт инфу" if abs(r)>=0.10 else ("слабо" if abs(r)>=0.04 else "ШУМ (гейт на шуме)")
        print(f"   {k:12}{r:>+8.2f}{nn:>6}  {v}")
    print(f"   |IC|>=0.10 значимо, 0.04-0.10 слабо, <0.04 = шум.\n")
    print(f"3) IC-DECAY (vol_ratio/di_dir/rsi_edge по горизонтам 3/6/12/18 баров):")
    for k in ("vol_ratio","di_dir","rsi_edge","dist_ema"):
        row=[f"   {k:12}"]
        for h in ("fwd3","fwd6","fwd12","fwd18"):
            r,_=ic([d[k] for d in D],[d[h] for d in D]); row.append(f"{(r if r else 0):+.2f}")
        print("".join(f"{x:>8}" if i else x for i,x in enumerate(row)))
    print(f"   (если IC не растёт с горизонтом — сигнал не про будущее)\n")
    print(f"4) РЕДАНДАНТНОСТЬ (|corr|>0.5 = одна фича тень другой):")
    arr={k:np.array([d[k] if d[k] is not None else np.nan for d in D]) for k in feats}
    for i,a in enumerate(feats):
        for b in feats[i+1:]:
            m=~(np.isnan(arr[a])|np.isnan(arr[b]))
            if m.sum()>15 and arr[a][m].std()>0 and arr[b][m].std()>0:
                c=np.corrcoef(arr[a][m],arr[b][m])[0,1]
                if abs(c)>=0.5: print(f"   {a} ~ {b}: r={c:+.2f}")
    print(f"\n5) ВАЛИДНОСТЬ РЕЖИМА (variance-ratio форвард-пути: >1 тренд, <1 разворот):")
    from collections import defaultdict
    byreg=defaultdict(list)
    for (sgn,p) in paths:
        if len(p)<8: continue
        rets=np.diff(np.log(p));
        if rets.std()==0: continue
        k=4; vr=(np.var(rets[:len(rets)//k*k].reshape(-1,k).sum(axis=1)))/(k*np.var(rets)) if len(rets)>=k else None
        byreg[p and None]  # noop
    # привязать VR к режиму через индекс
    for reg in sorted({d["regime"] for d in D}):
        vrs=[]
        for d,(sgn,p) in zip(D,paths):
            if d["regime"]!=reg or len(p)<8: continue
            r=np.diff(np.log(p))
            if r.std()==0: continue
            k=4; m=len(r)//k*k
            if m<k: continue
            vr=np.var(r[:m].reshape(-1,k).sum(axis=1))/(k*np.var(r))
            vrs.append(vr)
        if vrs: print(f"   {str(reg):10} n={len(vrs):3}  VR={np.mean(vrs):.2f}  → {'трендит' if np.mean(vrs)>1.1 else ('разворот' if np.mean(vrs)<0.9 else 'случайно')}")
    print(f"\n   ВЕРДИКТ внизу: если фичи-гейты в ШУМЕ + capture<<1 + режимы VR~1 → майн 'видит' шум и опаздывает.")

if __name__=="__main__":
    main()
