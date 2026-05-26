# -*- coding: utf-8 -*-
"""
entry_net_sim.py — NET-симуляция: наш вход (FIRE) vs правило (вето+разворот отката). Read-only.

Гипотеза (доказана на MFE): направление верное, входим поздно на всплеске объёма (истощение).
Правило = (1) ВЕТО на истощение vol_ratio>3.4; (2) ТРИГГЕР — вход на развороте отката (RSI-экстремум
за LOOKBACK + разворотный бар + не растянут). Оба входа РЫНОЧНЫЕ (убирает фантом-филл), одинаковый
ATR-стоп/тейк/холд/комса. Стоп считается первым на спорном баре (консервативно, без оптимизма).

Меряем: лифтит ли НЕТ-деньги, а не только MFE. Разложение: что вето отсеяло, что дал лучший вход.
"""
import sys, io, os, gzip, json, csv
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")

COMM=0.10            # % round-trip (≈0.05%/сторона OKX taker)
LOOKBACK=8; RSI_OS=42; RSI_OB=58; EXT_MAX=0.004   # триггер «откат-разворот»
VETO_VOL=3.40        # вето на истощение (из дерева: vol>3.4 = 84% дудов)
DEF_STOP_K=1.5; DEF_TP_K=2.5

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

def atr(hi,lo,cl,i,period=14):
    trs=[]
    for j in range(max(1,i-period+1),i+1):
        if None in (hi[j],lo[j],cl[j-1]): continue
        trs.append(max(hi[j]-lo[j],abs(hi[j]-cl[j-1]),abs(lo[j]-cl[j-1])))
    return sum(trs)/len(trs) if trs else None

def trigger(rows,t,sgn):
    if t<LOOKBACK: return False
    rsis=[fnum(rows[i].get("rsi")) for i in range(t-LOOKBACK,t+1)]
    if any(v is None for v in rsis[-2:]): return False
    pulled=any((v is not None and (v<RSI_OS if sgn>0 else v>RSI_OB)) for v in rsis)
    o=fnum(rows[t].get("open"));c=fnum(rows[t].get("close"));cp=fnum(rows[t-1].get("close"));e=fnum(rows[t].get("ema20"))
    if None in (o,c,cp,e): return False
    turn=sgn*(c-cp)>0 and sgn*(c-o)>0
    not_ext=sgn*(c/e-1)<=EXT_MAX
    return pulled and turn and not_ext

def sim_exit(idx,rows,hi,lo,cl,sgn,H,stop_k,tp_k):
    """вернёт (исход, net%) — стоп первым на спорном баре."""
    e=cl[idx]; a=atr(hi,lo,cl,idx)
    if not e or not a: return None
    stop=e-sgn*stop_k*a; tp=e+sgn*tp_k*a
    for j in range(idx+1,min(idx+H+1,len(rows))):
        adv=lo[j] if sgn>0 else hi[j]; fav=hi[j] if sgn>0 else lo[j]
        if None in (adv,fav): continue
        hit_stop=(adv<=stop) if sgn>0 else (adv>=stop)
        hit_tp=(fav>=tp) if sgn>0 else (fav<=tp)
        if hit_stop: return ("SL", sgn*(stop-e)/e*100-COMM)
        if hit_tp:   return ("TP", sgn*(tp-e)/e*100-COMM)
    last=cl[min(idx+H,len(rows)-1)]
    if last is None: return None
    return ("TIME", sgn*(last-e)/e*100-COMM)

def build():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    out=[]
    for s in snaps:
        sym=s["symbol"]; sgn=1 if s["side"] in ("buy","long") else -1
        ts_ms=int(s["ts_ms"]); H=max(6,int(s.get("hold_min",75))//5)
        rows=load5(sym,ts_ms)
        if len(rows)<14: continue
        hi=[fnum(r["high"]) for r in rows]; lo=[fnum(r["low"]) for r in rows]; cl=[fnum(r["close"]) for r in rows]
        fire=min(range(len(rows)),key=lambda i:abs(int(rows[i]["ts_ms"])-ts_ms))
        if fire<LOOKBACK or fire>=len(rows)-2: continue
        vol=fnum(s.get("context",{}).get("vol_ratio_sig"))
        a,b=max(LOOKBACK,fire-24),min(len(rows)-1,fire+H)
        rule_idx=next((t for t in range(a,b) if trigger(rows,t,sgn)),None)
        out.append({"sym":sym.replace("-USDT-SWAP",""),"sgn":sgn,"H":H,"fire":fire,"vol":vol,
                    "rows":rows,"hi":hi,"lo":lo,"cl":cl,"rule_idx":rule_idx})
    return out

def run(data,stop_k,tp_k):
    """вернёт сводку при заданной геометрии."""
    fire_nets=[]; rule_nets=[]; matched_fire=[]; matched_rule=[]
    veto_skip_fire=[]; notrig_skip_fire=[]; offs=[]; rule_oc=[]
    for d in data:
        fr=sim_exit(d["fire"],d["rows"],d["hi"],d["lo"],d["cl"],d["sgn"],d["H"],stop_k,tp_k)
        if fr is None: continue
        fire_nets.append(fr[1])
        if d["vol"] is not None and d["vol"]>VETO_VOL:
            veto_skip_fire.append(fr[1]); continue          # правило НЕ входит
        if d["rule_idx"] is None:
            notrig_skip_fire.append(fr[1]); continue         # нет разворота — пропуск
        rr=sim_exit(d["rule_idx"],d["rows"],d["hi"],d["lo"],d["cl"],d["sgn"],d["H"],stop_k,tp_k)
        if rr is None: continue
        rule_nets.append(rr[1]); rule_oc.append(rr[0])
        matched_fire.append(fr[1]); matched_rule.append(rr[1]); offs.append((d["rule_idx"]-d["fire"])*5)
    return dict(fire_nets=fire_nets,rule_nets=rule_nets,matched_fire=matched_fire,matched_rule=matched_rule,
               veto=veto_skip_fire,notrig=notrig_skip_fire,offs=offs,rule_oc=rule_oc)

def mean(a): return sum(a)/len(a) if a else float("nan")
def wr(nets): return 100*sum(1 for x in nets if x>0)/len(nets) if nets else float("nan")

def main():
    data=build(); n=len(data)
    print(f"=== NET-СИМ: {n} сигналов | комса {COMM}%/круг | стоп первым (консервативно) ===")
    print(f"    правило: ВЕТО vol>{VETO_VOL} + ТРИГГЕР откат-разворот (RSI<{RSI_OS}/>{RSI_OB} за {LOOKBACK*5}м, не растянут)\n")
    R=run(data,DEF_STOP_K,DEF_TP_K)
    fn,rn=R["fire_nets"],R["rule_nets"]
    print(f"--- ГЕОМЕТРИЯ ПО УМОЛЧАНИЮ: стоп {DEF_STOP_K}×ATR, тейк {DEF_TP_K}×ATR (R:R {DEF_TP_K/DEF_STOP_K:.1f}) ---")
    print(f"  НАШ ВХОД (FIRE):  сделок {len(fn):3}  ср.NET {mean(fn):+.2f}%  сумма {sum(fn):+.1f}%  WR {wr(fn):.0f}%")
    print(f"  ПРАВИЛО (RULE):   сделок {len(rn):3}  ср.NET {mean(rn):+.2f}%  сумма {sum(rn):+.1f}%  WR {wr(rn):.0f}%")
    from collections import Counter
    print(f"     исходы RULE: {dict(Counter(R['rule_oc']))}")
    print(f"\n  РАЗЛОЖЕНИЕ (откуда разница):")
    print(f"   • ВЕТО отсеяло {len(R['veto'])} сигналов — их FIRE-NET в среднем {mean(R['veto']):+.2f}% (если <0 → вето спасло)")
    print(f"   • нет разворота: пропущено {len(R['notrig'])} — их FIRE-NET {mean(R['notrig']):+.2f}%")
    mf,mr=R["matched_fire"],R["matched_rule"]
    print(f"   • НА ОДНИХ И ТЕХ ЖЕ {len(mf)} сделках:  FIRE {mean(mf):+.2f}% → RULE {mean(mr):+.2f}%  (дельта входа {mean(mr)-mean(mf):+.2f}%)")
    print(f"     правило входило в среднем {mean(R['offs']):+.0f} мин относительно нас")
    # робастность по геометрии
    print(f"\n--- РОБАСТНОСТЬ (сумма NET%, чтобы не зависеть от одной геометрии) ---")
    print(f"  {'стоп×тейк':>12} {'FIRE сумма':>12} {'RULE сумма':>12} {'RULE ср':>9} {'FIRE ср':>9}")
    for sk in (1.0,1.5,2.0):
        for tk in (1.5,2.5,3.5):
            r=run(data,sk,tk)
            print(f"  {sk:>4.1f}×{tk:<6.1f} {sum(r['fire_nets']):>12.1f} {sum(r['rule_nets']):>12.1f} {mean(r['rule_nets']):>9.2f} {mean(r['fire_nets']):>9.2f}")
    print(f"\n  ЧЕСТНО: вход рыночный у обоих (не лимитка бота); n мал; это историческая привязка к нашим же")
    print(f"  событиям (тест 'мог ли скринер сработать раньше/чище', не предсказание новых сетапов).")

if __name__=="__main__":
    main()
