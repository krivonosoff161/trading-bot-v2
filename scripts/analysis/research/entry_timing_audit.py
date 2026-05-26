# -*- coding: utf-8 -*-
"""
entry_timing_audit.py — КОГДА сработали vs КОГДА надо было + мат-подпись момента (read-only).

Для каждого недавнего сигнала: по 5m находим ИДЕАЛЬНЫЙ бар входа (макс. будущий ход),
тянем параметры на FIRE и IDEAL барах с 5m + 15m + 1H, side-нормализуем (для sell зеркало),
и считаем какие параметры СТАБИЛЬНО разделяют поздний вход от правильного (закономерность).

Usage: python scripts/analysis/research/entry_timing_audit.py
"""
import sys, io, os, gzip, json, csv
from datetime import datetime, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FEAT = os.path.join(ROOT, "logs", "features")
SNAP = os.path.join(ROOT, "logs", "signals", "signal_snapshot.jsonl")
PERIOD = "2026-05-24"

def fnum(x):
    try: return float(x)
    except: return None

def load_tf(tf, symbol, ts_ms):
    """бары символа на tf вокруг ts_ms (±1 день)."""
    rows=[]
    for off in (-1,0):
        d=datetime.utcfromtimestamp(ts_ms/1000 + off*86400).strftime("%Y-%m-%d")
        for ext in (".csv.gz",".csv"):
            p=os.path.join(FEAT,tf,symbol,d+ext)
            if os.path.exists(p):
                op=gzip.open if ext==".csv.gz" else open
                with op(p,"rt",encoding="utf-8",errors="ignore") as fh:
                    rows+=list(csv.DictReader(fh)); break
    seen=set(); out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]); out.append(r)
    return out

def bar_at(rows, ts_ms):
    cand=[r for r in rows if int(r["ts_ms"])<=ts_ms]
    return cand[-1] if cand else (rows[0] if rows else None)

def grab(rows, ts_ms, sgn):
    """side-нормализованные параметры на баре, покрывающем ts_ms."""
    r=bar_at(rows,ts_ms)
    if not r: return {}
    c=fnum(r.get("close")); e=fnum(r.get("ema20")); rsi=fnum(r.get("rsi"))
    di=fnum(r.get("di_spread")); sl=fnum(r.get("slope"))
    return {
        "vol_ratio": fnum(r.get("vol_ratio_sig")),
        "pullback%": (-sgn*(c/e-1)*100) if (c and e) else None,   # >0 = вход на откате ПРОТИВ тренда (глубже = лучше)
        "rsi_edge": ((50-rsi) if sgn>0 else (rsi-50)) if rsi is not None else None,  # >0 = экстремум в нашу сторону
        "adx": fnum(r.get("adx")),
        "adx_rising": 1.0 if str(r.get("adx_rising")).lower()=="true" else (0.0 if r.get("adx_rising") not in (None,"") else None),
        "di_spread_dir": (sgn*di) if di is not None else None,    # >0 = тренд в нашу сторону
        "bb_width%": fnum(r.get("bb_width_pct")),
        "bb_expanding": 1.0 if str(r.get("bb_expanding")).lower()=="true" else (0.0 if r.get("bb_expanding") not in (None,"") else None),
        "slope_dir": (sgn*sl) if sl is not None else None,
        "atr%": fnum(r.get("atr_pct")),
    }

def main():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip()]
    sigs=[s for s in snaps if s.get("ts","")>=PERIOD and s.get("entry_signal")=="ENTRY"]
    FIRE={}; IDEAL={}; offs=[]
    def add(d,k,v):
        if v is not None: d.setdefault(k,[]).append(v)
    print(f"=== {len(sigs)} сигналов | параметры 5m+15m+1H, FIRE→IDEAL ===")
    for s in sigs:
        sym=s["symbol"]; sgn=1 if s["side"] in ("buy","long") else -1
        ts_ms=int(s["ts_ms"]); H=max(6,int(s.get("hold_min",75))//5)
        f5=load_tf("5m",sym,ts_ms)
        if len(f5)<10: continue
        fire=min(range(len(f5)),key=lambda i:abs(int(f5[i]["ts_ms"])-ts_ms))
        cl=[fnum(r["close"]) for r in f5]; hi=[fnum(r["high"]) for r in f5]; lo=[fnum(r["low"]) for r in f5]
        def fav(c):
            b=0
            for j in range(c+1,min(c+H+1,len(f5))):
                ext=hi[j] if sgn>0 else lo[j]
                if cl[c] and ext is not None: b=max(b,sgn*(ext-cl[c])/cl[c]*100)
            return b
        a,b=max(0,fire-24),min(len(f5)-1,fire+H)
        ideal=max(range(a,b),key=fav) if b>a else fire
        offs.append((ideal-fire)*5)
        for tag,idx,store in (("FIRE",fire,FIRE),("IDEAL",ideal,IDEAL)):
            ts=int(f5[idx]["ts_ms"])
            p=grab(f5,ts,sgn)                       # 5m: vol_ratio,pullback,rsi_edge
            p15=grab(load_tf("15m",sym,ts),ts,sgn)  # 15m: adx,di,bb,slope,atr
            for k in ("vol_ratio","pullback%","rsi_edge"): add(store,k,p.get(k))
            for k in ("adx","adx_rising","di_spread_dir","bb_width%","bb_expanding","slope_dir","atr%"): add(store,k,p15.get(k))
    # сводка + закономерность
    keys=["vol_ratio","pullback%","rsi_edge","adx","adx_rising","di_spread_dir","bb_width%","bb_expanding","slope_dir","atr%"]
    print(f"\n идеальный вход в среднем {sum(offs)/len(offs):+.0f} мин относительно нашего (− = РАНЬШЕ)\n")
    print(f" {'параметр':14}{'FIRE':>9}{'IDEAL':>9}{'Δ':>9}  тренд (IDEAL отн. FIRE)")
    def mean(a): return sum(a)/len(a) if a else None
    rows=[]
    for k in keys:
        f,i=mean(FIRE.get(k,[])),mean(IDEAL.get(k,[]))
        if f is None or i is None: continue
        d=i-f; rng=max(abs(f),abs(i),1e-9)
        rows.append((k,f,i,d,abs(d)/rng))
        arrow = "ВЫШЕ" if d>0 else "НИЖЕ"
        print(f" {k:14}{f:9.2f}{i:9.2f}{d:+9.2f}  {arrow}")
    print("\n========== ЗАКОНОМЕРНОСТЬ (сорт по силе разделения) ==========")
    for k,f,i,d,strength in sorted(rows,key=lambda r:-r[4]):
        print(f"  {k:14} сила={strength:4.2f}  {f:.2f} → {i:.2f}")
    # composite «early-entry score»: z к IDEAL-направлению
    print("\n========== EARLY-ENTRY SCORE (composite, выше=ближе к идеальному моменту) ==========")
    import statistics
    allv={k:(FIRE.get(k,[])+IDEAL.get(k,[])) for k in keys}
    def z(k,v):
        a=allv[k]
        if len(a)<2 or v is None: return 0.0
        m=statistics.mean(a); sd=statistics.pstdev(a) or 1e-9
        # направление: куда сдвигается IDEAL
        fi,ii=mean(FIRE.get(k,[])),mean(IDEAL.get(k,[]))
        dirn=1 if (ii or 0)>=(fi or 0) else -1
        return dirn*(v-m)/sd
    def score(store):
        per=[]
        n=len(next(iter(store.values())))
        for idx in range(n):
            sc=0;c=0
            for k in keys:
                if k in store and idx<len(store[k]):
                    sc+=z(k,store[k][idx]); c+=1
            if c: per.append(sc/c)
        return mean(per)
    sf,si=score(FIRE),score(IDEAL)
    print(f"  FIRE score:  {sf:+.2f}")
    print(f"  IDEAL score: {si:+.2f}")
    print(f"  разделение:  {si-sf:+.2f}  (чем больше, тем чётче подпись момента)")

if __name__=="__main__":
    main()
