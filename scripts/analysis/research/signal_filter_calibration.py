# -*- coding: utf-8 -*-
"""
signal_filter_calibration.py — БОЛЬШОЙ разбор: фильтр кривой или тайминг? (read-only, вся история)

По каждому сигналу (snapshot) с покрытием 5m-фич: параметры на входе (из context),
исход (из labels), реальный захват хода (fire_fav) и потолок (ideal_fav).
Отвечает: (1) хорош ли ОТБОР направления (был ли ход вообще), (2) что отделяет рабочие сигналы
от дудов = калибровка фильтра, (3) сколько теряем на ТАЙМИНГЕ.
"""
import sys, io, os, gzip, json, csv, statistics
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
LAB=os.path.join(ROOT,"logs","signals","main_signals_labels.jsonl")

def fnum(x):
    try: return float(x)
    except: return None
def load5(sym,ts_ms):
    rows=[]
    for off in (-1,0):
        d=datetime.utcfromtimestamp(ts_ms/1000+off*86400).strftime("%Y-%m-%d")
        p=os.path.join(F5,sym,d+".csv.gz"); p2=os.path.join(F5,sym,d+".csv")
        fp = p if os.path.exists(p) else (p2 if os.path.exists(p2) else None)
        if not fp: continue
        op=gzip.open if fp.endswith(".gz") else open
        with op(fp,"rt",encoding="utf-8",errors="ignore") as fh: rows+=list(csv.DictReader(fh))
    seen=set();out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]);out.append(r)
    return out

def main():
    labels={l["signal_id"]:l for l in (json.loads(x) for x in open(LAB,encoding="utf-8") if x.strip())}
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    data=[]
    for s in snaps:
        sym=s["symbol"]; sgn=1 if s["side"] in ("buy","long") else -1
        ts_ms=int(s["ts_ms"]); H=max(6,int(s.get("hold_min",75))//5)
        rows=load5(sym,ts_ms)
        if len(rows)<12: continue
        cl=[fnum(r["close"]) for r in rows]; hi=[fnum(r["high"]) for r in rows]; lo=[fnum(r["low"]) for r in rows]
        fire=min(range(len(rows)),key=lambda i:abs(int(rows[i]["ts_ms"])-ts_ms))
        def fav(c):
            b=0
            for j in range(c+1,min(c+H+1,len(rows))):
                ext=hi[j] if sgn>0 else lo[j]
                if cl[c] and ext is not None: b=max(b,sgn*(ext-cl[c])/cl[c]*100)
            return b
        a,b=max(0,fire-24),min(len(rows)-1,fire+H)
        ideal=max(range(a,b),key=fav) if b>a else fire
        ctx=s.get("context",{}); ind=s.get("indicators",{})
        e=fnum(s.get("entry")); e15=None
        try: e15=fnum(ind.get("15m",{}).get("ema20"))
        except: pass
        di=fnum(ctx.get("plus_di_1h")); dm=fnum(ctx.get("minus_di_1h"))
        lab=labels.get(s["signal_id"],{})
        data.append({
            "sym":sym.replace("-USDT-SWAP",""),"side":s["side"],"regime":s.get("regime"),"style":s.get("trade_style"),
            "outcome":lab.get("outcome"),"fire_fav":fav(fire),"ideal_fav":fav(ideal),"off_min":(ideal-fire)*5,
            "vol_ratio":fnum(ctx.get("vol_ratio_sig")),"adx_1h":fnum(ctx.get("adx_1h")),
            "adx_rising":1.0 if ctx.get("adx_1h_rising") is True else (0.0 if ctx.get("adx_1h_rising") is False else None),
            "day_pos":fnum(ctx.get("day_position")),
            "di_dir":(sgn*(di-dm)) if (di is not None and dm is not None) else None,
            "dist_ema":(-sgn*(e/e15-1)*100) if (e and e15) else None,  # >0 = вход на откате против тренда
            "bb_expanding":1.0 if ctx.get("bb_expanding") is True else (0.0 if ctx.get("bb_expanding") is False else None),
        })
    n=len(data); print(f"=== РАЗОБРАНО {n} сигналов (вся история с покрытием 5m) ===\n")
    # 1. качество ОТБОРА направления
    movers=[d for d in data if d["ideal_fav"]>=1.0]
    print(f"1) ОТБОР НАПРАВЛЕНИЯ: у {len(movers)}/{n} = {100*len(movers)//n}% был ход >=1%% (если б вовремя вошли)")
    print(f"   значит сигнал/направление {'НОРМ — проблема тайминг/фильтр входа' if len(movers)>n*0.5 else 'СЛАБЫЙ — проблема в самом сигнале'}")
    # 2. ТАЙМИНГ
    ff=[d['fire_fav'] for d in data]; iff=[d['ideal_fav'] for d in data]
    print(f"\n2) ТАЙМИНГ: реальный захват {statistics.mean(ff):+.2f}%% vs потолок {statistics.mean(iff):+.2f}%% — теряем {statistics.mean(iff)-statistics.mean(ff):.2f}%%/сигнал на входе")
    print(f"   идеал в среднем {statistics.mean([d['off_min'] for d in data]):+.0f} мин относительно нас")
    # 3. что отделяет РАБОЧИЕ от ДУДОВ (калибровка фильтра)
    med=statistics.median(ff)
    good=[d for d in data if d["fire_fav"]>=med]; bad=[d for d in data if d["fire_fav"]<med]
    print(f"\n3) ФИЛЬТР-КАЛИБРОВКА: параметры РАБОЧИХ (захват>={med:.2f}%, n={len(good)}) vs ДУДОВ (n={len(bad)})")
    print(f"   {'параметр':12}{'РАБОЧИЕ':>10}{'ДУДЫ':>10}{'разрыв':>9}")
    rk=[]
    for k in ("vol_ratio","adx_1h","adx_rising","day_pos","di_dir","dist_ema","bb_expanding"):
        gv=[d[k] for d in good if d[k] is not None]; bv=[d[k] for d in bad if d[k] is not None]
        if len(gv)<3 or len(bv)<3: continue
        mg,mb=statistics.mean(gv),statistics.mean(bv); rng=max(abs(mg),abs(mb),1e-9)
        rk.append((k,mg,mb,abs(mg-mb)/rng)); print(f"   {k:12}{mg:10.2f}{mb:10.2f}{abs(mg-mb)/rng:9.2f}")
    print("\n   СИЛЬНЕЙШИЕ разделители (калибровать фильтр по ним):")
    for k,mg,mb,st in sorted(rk,key=lambda r:-r[3])[:4]:
        print(f"     {k}: рабочие {mg:.2f} / дуды {mb:.2f}")
    # 4. по режиму/стилю
    print("\n4) ПО РЕЖИМУ×СТИЛЮ (средний захват):")
    cells={}
    for d in data: cells.setdefault((d["regime"],d["style"]),[]).append(d["fire_fav"])
    for k in sorted(cells,key=lambda k:-statistics.mean(cells[k])):
        v=cells[k]; print(f"   {str(k[0])[:6]:6}/{str(k[1])[:5]:5}  n={len(v):3}  захват {statistics.mean(v):+.2f}%")

if __name__=="__main__":
    main()
