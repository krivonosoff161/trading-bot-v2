# -*- coding: utf-8 -*-
"""
idea1_compression_breakout.py — КАМПАНИЯ идея №1: ранняя фаза крупного хода. Read-only.

Механизм (причинный): волатильность СЖИМАЕТСЯ (узкая консолидация) → ПРОБОЙ с расширением диапазона =
ранний старт хода, ДО «подтверждённого импульса» (где наш скринер опаздывает). Вход на баре пробоя,
стоп структурный (противоположная сторона консолидации), выход стоп/время.
Тест: train(11-18.05) / holdout(19-26.05), 3 варианта порогов. Меряем NET (не MFE) + ПОКРЫТИЕ крупных ходов
(сравнение с нашими 0-1%). Гейт: net+ на holdout И покрытие крупных ходов выше нашего.
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
from statistics import mean, median
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
COMM=0.10; CONSOL=12; H=48   # консолидация 1ч, холд до 4ч
TRAIN_END="2026-05-18"; START="2026-05-11"; END="2026-05-26"

def fnum(x):
    try: return float(x)
    except: return None
def load(sym,day):
    for ext,op in ((".csv.gz",gzip.open),(".csv",open)):
        p=os.path.join(F5,sym,day+ext)
        if os.path.exists(p):
            with op(p,"rt",encoding="utf-8",errors="ignore") as fh: return list(csv.DictReader(fh))
    return []
def daylist():
    d=datetime.strptime(START,"%Y-%m-%d"); e=datetime.strptime(END,"%Y-%m-%d"); out=[]
    while d<=e: out.append(d.strftime("%Y-%m-%d")); d+=timedelta(days=1)
    return out

def detect_and_sim(rows, comp_max, exp_min, vol_min):
    """вернёт список сделок: (ts, dir, net, mfe, outcome)."""
    n=len(rows)
    cl=[fnum(r.get("close")) for r in rows]; hi=[fnum(r.get("high")) for r in rows]; lo=[fnum(r.get("low")) for r in rows]
    op=[fnum(r.get("open")) for r in rows]; ts=[int(r["ts_ms"]) for r in rows]
    vr=[fnum(r.get("vol_ratio_sig")) for r in rows]
    trades=[]
    i=CONSOL
    while i<n-2:
        if None in (cl[i],hi[i],lo[i],op[i]): i+=1; continue
        win_hi=max(h for h in hi[i-CONSOL:i] if h is not None)
        win_lo=min(l for l in lo[i-CONSOL:i] if l is not None)
        if not win_hi or not win_lo or win_lo<=0: i+=1; continue
        consol_rng=(win_hi-win_lo)/win_lo*100
        prior_rng=[(hi[j]-lo[j])/cl[j]*100 for j in range(i-CONSOL,i) if cl[j]]
        cur_rng=(hi[i]-lo[i])/cl[i]*100 if cl[i] else 0
        avg_rng=mean(prior_rng) if prior_rng else 1e-9
        expand = cur_rng/avg_rng if avg_rng>0 else 0
        vol_ok = (vol_min is None) or (vr[i] is not None and vr[i]>=vol_min)
        sgn=0
        if consol_rng<=comp_max and expand>=exp_min and vol_ok:
            if cl[i]>win_hi: sgn=1
            elif cl[i]<win_lo: sgn=-1
        if sgn==0: i+=1; continue
        e=cl[i]; stop=win_lo if sgn>0 else win_hi
        if (sgn>0 and stop>=e) or (sgn<0 and stop<=e): i+=1; continue
        oc="TIME"; exitp=cl[min(i+H,n-1)]; mfe=0.0
        for j in range(i+1,min(i+H+1,n)):
            adv=lo[j] if sgn>0 else hi[j]; fav=hi[j] if sgn>0 else lo[j]
            if fav is not None and e: mfe=max(mfe,sgn*(fav-e)/e*100)
            if adv is not None and ((adv<=stop) if sgn>0 else (adv>=stop)):
                oc="SL"; exitp=stop; break
        net=sgn*(exitp-e)/e*100-COMM if (e and exitp) else 0
        trades.append((ts[i],sgn,net,mfe,oc))
        i+=H//2  # не переоткрывать на каждом баре одной волны
    return trades

def best_moves(rows, thr=5.0, W=24):
    cl=[fnum(r.get("close")) for r in rows]; hi=[fnum(r.get("high")) for r in rows]; lo=[fnum(r.get("low")) for r in rows]; ts=[int(r["ts_ms"]) for r in rows]
    out=[]
    for i in range(len(rows)):
        if not cl[i]: continue
        up=dn=0.0
        for j in range(i+1,min(i+W+1,len(rows))):
            if hi[j]: up=max(up,(hi[j]-cl[i])/cl[i]*100)
            if lo[j]: dn=max(dn,(cl[i]-lo[j])/cl[i]*100)
        if up>=thr: out.append((ts[i],1,up))
        if dn>=thr: out.append((ts[i],-1,dn))
    # сжать в события (не каждый бар): берём локальные максимумы, разрежаем по 1ч
    out.sort(key=lambda x:-x[2]); picked=[]
    for t,d,s in out:
        if all(abs(t-p[0])>3600000 or d!=p[1] for p in picked): picked.append((t,d,s))
    return picked

def main():
    days=daylist(); syms=sorted(os.listdir(F5))
    variants=[("A: comp<=2.5%, exp>=1.5",2.5,1.5,None),
              ("B: comp<=1.5%, exp>=2.0",1.5,2.0,None),
              ("C: comp<=2.0%, exp>=1.5, vol>=1.2",2.0,1.5,1.2)]
    # предрасчёт крупных ходов вселенной (для покрытия)
    bigmoves={}  # (sym,day)->list of (ts,dir,size)
    for s in syms:
        for day in days:
            rows=load(s,day)
            if len(rows)>=30: bigmoves[(s,day)]=best_moves(rows,5.0)
    total_big=sum(len(v) for v in bigmoves.values())
    print(f"=== ИДЕЯ №1: сжатие→пробой | {len(syms)} символов, {START}..{END} | крупных ходов ≥5%: {total_big} ===")
    print(f"(гейт: net+ на HOLDOUT 19-26.05 И покрытие крупных ходов заметно выше наших ~1%)\n")
    for name,cmax,emin,vmin in variants:
        tr_train=[]; tr_hold=[]; trig_pts=[]
        for s in syms:
            for day in days:
                rows=load(s,day)
                if len(rows)<30: continue
                trs=detect_and_sim(rows,cmax,emin,vmin)
                for t in trs:
                    trig_pts.append((s,day,t[0],t[1]))
                    (tr_train if day<=TRAIN_END else tr_hold).append(t)
        def stat(trs):
            if not trs: return "n=0"
            nets=[x[2] for x in trs]; mfes=[x[3] for x in trs]; ocs=[x[4] for x in trs]
            return (f"n={len(trs):3} NET ср{mean(nets):+.2f}% сум{sum(nets):+.1f}% WR{100*sum(1 for x in nets if x>0)//len(nets)}% "
                    f"MFE{mean(mfes):+.1f}% SL{100*ocs.count('SL')//len(ocs)}%")
        # покрытие крупных ходов
        covered=0
        for (s,day),moves in bigmoves.items():
            for mt,md,ms in moves:
                if any(ss==s and dd==day and dr==md and (mt-30*60000)<=tt<=(mt+30*60000) for ss,dd,tt,dr in trig_pts):
                    covered+=1
        print(f"  [{name}]")
        print(f"     TRAIN  {stat(tr_train)}")
        print(f"     HOLD   {stat(tr_hold)}")
        print(f"     покрытие крупных ходов ≥5%: {covered}/{total_big} = {100*covered//max(1,total_big)}%  (наш скринер был ~1%)")
        print()

if __name__=="__main__":
    main()
