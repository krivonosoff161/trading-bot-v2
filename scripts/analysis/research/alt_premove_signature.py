# -*- coding: utf-8 -*-
"""
alt_premove_signature.py — ЧТО ИДЁТ ПЕРЕД РОСТОМ альта (прекурсоры пампа). Read-only.

Для каждого КРУПНОГО движения (forward ≥5% за 1.5ч) находим СТАРТ (до него не пампило) и смотрим
причинную сигнатуру баров ДО старта из тейпа: ускорение объёма, накопление CVD (покупатели заранее),
сжатие диапазона, дрейф. Сравниваем PRE-move vs ФОН — что СТАТИСТИЧЕСКИ отличает момент перед пампом.
Это alt-специфика на коротком горизонте (а не длинные окна майоров). Тейп = реальный объём+агрессор.
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
from statistics import mean, pstdev
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
TAPE=r"E:\trading-data\ticks"
W_FWD=18; PRE=12; UP_THR=5.0      # forward 1.5ч, прекурсор-окно 1ч, порог пампа 5%

def bars5(path):
    op=gzip.open if path.endswith(".gz") else open
    out=[]; cur=None
    with op(path,"rt",encoding="utf-8",errors="ignore") as fh:
        r=csv.reader(fh); next(r,None)
        for row in r:
            if len(row)<6: continue
            try: t=int(row[0]); side=row[3]; p=float(row[4]); z=float(row[5])
            except: continue
            b=(t//300000)*300000
            if cur is None or b!=cur["t"]:
                if cur is not None: out.append(cur)
                cur={"t":b,"o":p,"h":p,"l":p,"c":p,"buy":0.0,"sell":0.0}
            if p>cur["h"]:cur["h"]=p
            if p<cur["l"]:cur["l"]=p
            cur["c"]=p
            if side=="buy": cur["buy"]+=z
            else: cur["sell"]+=z
    if cur is not None: out.append(cur)
    return out

def load_sym(sym):
    d=datetime(2026,5,11); e=datetime(2026,5,26); allb=[]
    base=os.path.join(TAPE,sym)
    if not os.path.isdir(base): return []
    while d<=e:
        p=os.path.join(base,d.strftime("%Y-%m-%d")+".csv.gz")
        if os.path.exists(p):
            try: allb+=bars5(p)
            except Exception: pass
        d+=timedelta(days=1)
    seen=set(); out=[]
    for b in sorted(allb,key=lambda x:x["t"]):
        if b["t"] not in seen: seen.add(b["t"]); out.append(b)
    return out

def features_at(b, i):
    """причинные прекурсор-фичи на баре i (только прошлое/текущее)."""
    c=[x["c"] for x in b]; hi=[x["h"] for x in b]; lo=[x["l"] for x in b]
    vol=[x["buy"]+x["sell"] for x in b]; dlt=[x["buy"]-x["sell"] for x in b]
    if i<PRE+3 or not c[i]: return None
    pv=vol[i-PRE:i]; mpv=mean(pv) if pv else 0
    vol_accel = vol[i]/mpv if mpv>0 else None
    recent=mean(vol[i-3:i]); older=mean(vol[i-PRE:i-3])
    vol_buildup = recent/older if older>0 else None
    sv=sum(vol[i-6:i+1]); cvd_dir = sum(dlt[i-6:i+1])/sv if sv>0 else None
    rng=[(hi[j]-lo[j])/c[j]*100 for j in range(i-PRE,i) if c[j]]
    cur_rng=(hi[i]-lo[i])/c[i]*100 if c[i] else None
    range_ratio = cur_rng/mean(rng) if (cur_rng is not None and rng and mean(rng)>0) else None
    compression = mean(rng) if rng else None        # низкий = сжато
    prior_drift = (c[i]/c[i-PRE]-1)*100 if c[i-PRE] else None
    return dict(vol_accel=vol_accel, vol_buildup=vol_buildup, cvd_dir=cvd_dir,
                range_ratio=range_ratio, compression=compression, prior_drift=prior_drift)

def fwd_up(b,i):
    c=b[i]["c"]; best=0.0
    for j in range(i+1,min(i+W_FWD+1,len(b))):
        if c and b[j]["h"]: best=max(best,(b[j]["h"]-c)/c*100)
    return best

def main():
    syms=[s for s in sorted(os.listdir(TAPE)) if os.path.isdir(os.path.join(TAPE,s))]
    pre_feats=[]; base_feats=[]; nmoves=0; nbars=0
    for sym in syms:
        b=load_sym(sym)
        if len(b)<60: continue
        i=PRE+3
        while i<len(b)-2:
            f=features_at(b,i)
            if f is None: i+=1; continue
            nbars+=1
            if nbars%5==0: base_feats.append(f)            # фон (подвыборка)
            up=fwd_up(b,i)
            prior6=(b[i]["c"]/b[i-6]["c"]-1)*100 if b[i-6]["c"] else 0
            if up>=UP_THR and prior6<1.5:                   # старт пампа (ещё не пампило)
                pre_feats.append(f); nmoves+=1; i+=W_FWD     # не считать один памп много раз
            else: i+=1
    keys=["vol_accel","vol_buildup","cvd_dir","range_ratio","compression","prior_drift"]
    print(f"=== ПРЕКУРСОРЫ ПАМПА: {nmoves} стартов крупных движений ≥{UP_THR}% (тейп, {len(syms)} альтов) ===")
    print(f"(сигнатура баров ПЕРЕД стартом vs фон — что причинно отличает момент перед ростом)\n")
    print(f"  {'фича':14}{'ПЕРЕД пампом':>14}{'ФОН':>10}{'разделение':>12}")
    rk=[]
    for k in keys:
        pv=[f[k] for f in pre_feats if f.get(k) is not None and np.isfinite(f[k])]
        bv=[f[k] for f in base_feats if f.get(k) is not None and np.isfinite(f[k])]
        if len(pv)<10 or len(bv)<10: continue
        mp=mean(pv); mb=mean(bv); sd=pstdev(bv) or 1e-9
        sep=(mp-mb)/sd                                       # на сколько σ фона отличается pre-move
        rk.append((k,mp,mb,sep))
    for k,mp,mb,sep in sorted(rk,key=lambda r:-abs(r[3])):
        print(f"  {k:14}{mp:>14.2f}{mb:>10.2f}{sep:>+11.2f}σ")
    print(f"\n  |разделение|>0.3σ = фича реально ведёт перед пампом → строить ранний вход на ней.")
    print(f"  (vol_accel/buildup высок + cvd_dir>0 + compression низок ПЕРЕД ростом = накопление+ignition)")

if __name__=="__main__":
    main()
