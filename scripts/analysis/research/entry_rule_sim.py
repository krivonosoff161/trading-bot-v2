# -*- coding: utf-8 -*-
"""
entry_rule_sim.py — тест ПРИЧИННОГО триггера «истощение отката» vs наш поздний вход.

Гипотеза: направление верное, входим поздно (на подтверждении). Правило ловит откат+разворот
ДО пробоя. Сравниваем на недавних сигналах: наш FIRE vs RULE vs IDEAL — захват хода и просадка (нож).
Триггер причинный (только прошлые бары). fav/mae — только для оценки. Read-only.
"""
import sys, io, os, gzip, json, csv
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FEAT=os.path.join(ROOT,"logs","features","5m"); SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
PERIOD="2026-05-24"
LOOKBACK=6; RSI_OS=38; RSI_OB=62; EXT_MAX=0.003   # параметры триггера

def fnum(x):
    try: return float(x)
    except: return None
def load5(sym,ts_ms):
    rows=[]
    for off in (-1,0):
        d=datetime.utcfromtimestamp(ts_ms/1000+off*86400).strftime("%Y-%m-%d")
        for ext in (".csv.gz",".csv"):
            p=os.path.join(FEAT,sym,d+ext)
            if os.path.exists(p):
                op=gzip.open if ext==".csv.gz" else open
                with op(p,"rt",encoding="utf-8",errors="ignore") as fh: rows+=list(csv.DictReader(fh))
                break
    seen=set();out=[]
    for r in sorted(rows,key=lambda r:int(r["ts_ms"])):
        if r["ts_ms"] not in seen: seen.add(r["ts_ms"]);out.append(r)
    return out

def trigger(rows,t,sgn):
    """причинный: откат (RSI-экстремум за LOOKBACK) + разворотный бар сейчас + не растянуты."""
    if t<LOOKBACK: return False
    rsis=[fnum(rows[i].get("rsi")) for i in range(t-LOOKBACK,t+1)]
    if any(v is None for v in rsis[-2:]): return False
    pulled = any((v is not None and (v<RSI_OS if sgn>0 else v>RSI_OB)) for v in rsis)
    o=fnum(rows[t].get("open")); c=fnum(rows[t].get("close")); cp=fnum(rows[t-1].get("close")); e=fnum(rows[t].get("ema20"))
    if None in (o,c,cp,e): return False
    turn = sgn*(c-cp)>0 and sgn*(c-o)>0
    not_ext = sgn*(c/e-1) <= EXT_MAX
    return pulled and turn and not_ext

def main():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip()]
    sigs=[s for s in snaps if s.get("ts","")>=PERIOD and s.get("entry_signal")=="ENTRY"]
    FAV={"fire":[],"rule":[],"ideal":[]}; MAE={"fire":[],"rule":[]}; found=0; offs=[]
    print(f"=== {len(sigs)} сигналов: FIRE vs RULE(откат-разворот) vs IDEAL ===")
    print(f" {'пара':6}{'side':5}{'fire_fav':>9}{'rule_fav':>9}{'rule_mae':>9}{'ideal':>8}  rule-offset")
    for s in sigs:
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
        def mae(c):
            w=0
            for j in range(c+1,min(c+H+1,len(rows))):
                adv=lo[j] if sgn>0 else hi[j]
                if cl[c] and adv is not None: w=min(w,sgn*(adv-cl[c])/cl[c]*100)
            return w
        a,b=max(LOOKBACK,fire-24),min(len(rows)-1,fire+H)
        ideal=max(range(a,b),key=fav) if b>a else fire
        rule=next((t for t in range(a,b) if trigger(rows,t,sgn)), None)
        symn=sym.replace("-USDT-SWAP","")
        FAV["fire"].append(fav(fire)); FAV["ideal"].append(fav(ideal)); MAE["fire"].append(mae(fire))
        if rule is not None:
            found+=1; FAV["rule"].append(fav(rule)); MAE["rule"].append(mae(rule)); offs.append((rule-fire)*5)
            print(f" {symn:6}{s['side']:5}{fav(fire):+9.2f}{fav(rule):+9.2f}{mae(rule):+9.2f}{fav(ideal):+8.2f}  {(rule-fire)*5:+d} мин")
        else:
            print(f" {symn:6}{s['side']:5}{fav(fire):+9.2f}{'—':>9}{'—':>9}{fav(ideal):+8.2f}  нет триггера")
    def avg(a): return sum(a)/len(a) if a else float('nan')
    print(f"\n===== СВОДКА (n={len(sigs)}, правило сработало в {found}) =====")
    print(f"  средний ЗАХВАТ хода:  FIRE {avg(FAV['fire']):+.2f}%  |  RULE {avg(FAV['rule']):+.2f}%  |  IDEAL(потолок) {avg(FAV['ideal']):+.2f}%")
    print(f"  средняя ПРОСАДКА (нож): FIRE {avg(MAE['fire']):+.2f}%  |  RULE {avg(MAE['rule']):+.2f}%")
    print(f"  правило входило в среднем {avg(offs):+.0f} мин относительно нас (− = раньше)")
    if avg(FAV['rule'])>avg(FAV['fire']): print("  → RULE ловит БОЛЬШЕ хода, чем наш вход")
    print(f"\n  параметры триггера: LOOKBACK={LOOKBACK}(30м), RSI_OS<{RSI_OS}/OB>{RSI_OB}, не растянут >{EXT_MAX*100:.1f}% за EMA")

if __name__=="__main__":
    main()
