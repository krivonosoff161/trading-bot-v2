# -*- coding: utf-8 -*-
"""
composite_setup.py — КОМПОЗИТ: ищем сетап как СОВОКУПНОСТЬ сигналов (идея трейдера). Read-only.

Не привязан к нашим сигналам (в отличие от V1) — сканируем ВСЮ вселенную и берём бары, где совпало
несколько условий сразу: ТРЕНД (ema rising + цена по тренду) + ОТКАТ (к ema / RSI остыл) + РАЗВОРОТНЫЙ
бар + АНТИ-CLIMAX (не растянут, не объёмный пик). Вход на развороте отката в тренде = «купи дип в тренде».
Walk-forward по 4 окнам мая. Гейт: NET+ в большинстве окон (3/4) = робастный сетап.
"""
import sys, io, os, gzip, csv
from datetime import datetime, timedelta
from statistics import mean
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
COMM=0.10; H=24   # холд до 2ч
WINDOWS=[("11-14","2026-05-11","2026-05-14"),("15-18","2026-05-15","2026-05-18"),
         ("19-22","2026-05-19","2026-05-22"),("23-26","2026-05-23","2026-05-26")]

def fnum(x):
    try: return float(x)
    except: return None
def load(sym,day):
    for ext,op in ((".csv.gz",gzip.open),(".csv",open)):
        p=os.path.join(F5,sym,day+ext)
        if os.path.exists(p):
            with op(p,"rt",encoding="utf-8",errors="ignore") as fh: return list(csv.DictReader(fh))
    return []
def alldays():
    d=datetime(2026,5,11); e=datetime(2026,5,26); o=[]
    while d<=e: o.append(d.strftime("%Y-%m-%d")); d+=timedelta(days=1)
    return o

def rsi_arr(c,p=14):
    n=len(c);o=[None]*n
    if n<p+1: return o
    g=l=0.0
    for i in range(1,p+1):
        if c[i] is None or c[i-1] is None: return o
        ch=c[i]-c[i-1]; g+=max(ch,0); l+=max(-ch,0)
    ag=g/p; al=l/p; o[p]=100-100/(1+ag/al) if al>0 else 100.0
    for i in range(p+1,n):
        if c[i] is None or c[i-1] is None: o[i]=o[i-1]; continue
        ch=c[i]-c[i-1]; ag=(ag*(p-1)+max(ch,0))/p; al=(al*(p-1)+max(-ch,0))/p
        o[i]=100-100/(1+ag/al) if al>0 else 100.0
    return o

def trades_for(rows, comp_vol_max, rsi_lo, rsi_hi):
    c=[fnum(r.get("close")) for r in rows]; hi=[fnum(r.get("high")) for r in rows]; lo=[fnum(r.get("low")) for r in rows]
    op=[fnum(r.get("open")) for r in rows]; ema=[fnum(r.get("ema20")) for r in rows]
    vr=[fnum(r.get("vol_ratio_sig")) for r in rows]; ts=[int(r["ts_ms"]) for r in rows]
    rsi=rsi_arr(c); n=len(rows); out=[]; i=20
    while i<n-2:
        C,O,E=c[i],op[i],ema[i]; Cp=c[i-1]
        if None in (C,O,E,Cp) or not E: i+=1; continue
        sgn=0
        # тренд: ema поднимается/опускается за 6 баров
        e6=ema[i-6]
        if e6:
            up_trend = E>e6 and C>E*0.998
            dn_trend = E<e6 and C<E*1.002
            # откат остыл: RSI был в зоне отката за последние 4 бара
            seg=[rsi[j] for j in range(i-4,i+1) if rsi[j] is not None]
            pulled_up = any(v<rsi_lo for v in seg)      # для long: RSI падал
            pulled_dn = any(v>rsi_hi for v in seg)       # для short: RSI рос
            turn_up = C>Cp and C>O
            turn_dn = C<Cp and C<O
            ext = abs(C/E-1)*100
            vol_ok = (vr[i] is None) or (vr[i]<=comp_vol_max)
            if up_trend and pulled_up and turn_up and ext<=0.6 and vol_ok: sgn=1
            elif dn_trend and pulled_dn and turn_dn and ext<=0.6 and vol_ok: sgn=-1
        if sgn==0: i+=1; continue
        E_px=C; stop=min(lo[i-5:i+1]) if sgn>0 else max(hi[i-5:i+1])
        if (sgn>0 and stop>=E_px) or (sgn<0 and stop<=E_px): i+=1; continue
        oc="TIME"; exitp=c[min(i+H,n-1)]
        risk=abs(E_px-stop); tp=E_px+sgn*2*risk
        for j in range(i+1,min(i+H+1,n)):
            adv=lo[j] if sgn>0 else hi[j]; fav=hi[j] if sgn>0 else lo[j]
            if adv is not None and ((adv<=stop) if sgn>0 else (adv>=stop)): oc="SL"; exitp=stop; break
            if fav is not None and ((fav>=tp) if sgn>0 else (fav<=tp)): oc="TP"; exitp=tp; break
        net=sgn*(exitp-E_px)/E_px*100-COMM if (E_px and exitp) else 0
        out.append((ts[i],net,oc)); i+=6
    return out

def main():
    syms=sorted(os.listdir(F5)); days=alldays()
    variants=[("anti-vol<=2.0, RSI 40/60",2.0,40,60),
              ("anti-vol<=1.5, RSI 35/65",1.5,35,65),
              ("anti-vol<=3.0, RSI 45/55",3.0,45,55)]
    print(f"=== КОМПОЗИТ: тренд+откат+разворот+анти-climax | {len(syms)} символов | walk-forward 4 окна ===")
    print(f"(гейт: NET+ в 3/4 окон = робастный сетап)\n")
    # day -> window
    def win_of(day):
        for nm,a,b in WINDOWS:
            if a<=day<=b: return nm
        return None
    for name,cv,rl,rh in variants:
        bywin={w[0]:[] for w in WINDOWS}
        for s in syms:
            for day in days:
                rows=load(s,day)
                if len(rows)<30: continue
                w=win_of(day)
                if w: bywin[w]+=trades_for(rows,cv,rl,rh)
        print(f"  [{name}]")
        pos=0
        for nm,a,b in WINDOWS:
            tr=bywin[nm]
            if not tr: print(f"     окно {nm}: n=0"); continue
            nets=[x[1] for x in tr]; ocs=[x[2] for x in tr]
            m=mean(nets); wr=100*sum(1 for x in nets if x>0)//len(nets)
            if m>0: pos+=1
            print(f"     окно {nm}: n={len(tr):4} NET ср{m:+.2f}% сум{sum(nets):+.1f}% WR{wr}% TP{100*ocs.count('TP')//len(ocs)}%/SL{100*ocs.count('SL')//len(ocs)}%")
        print(f"     → положительных окон: {pos}/4  {'✅ РОБАСТНО' if pos>=3 else '❌ нет'}\n")

if __name__=="__main__":
    main()
