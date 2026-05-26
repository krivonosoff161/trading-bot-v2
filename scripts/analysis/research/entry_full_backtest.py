# -*- coding: utf-8 -*-
"""
entry_full_backtest.py — ПОЛНЫЙ бэк на ТЕЙПЕ (тик-точные филлы). Read-only.

Апгрейд к entry_net_sim: вместо 5m-бара — реальные тики (E:\\trading-data\\ticks).
  • вход = первый РЕАЛЬНЫЙ принт нашего агрессора после решения (переход через спред = слиппедж из данных),
  • стоп/тейк = фактический ПОРЯДОК тиков (никакого «стоп первым» допущения — вижу реальный путь),
  • SL market = филл по пробившему принту (консервативно), TP limit = по уровню, TIME = market по последнему.
Триггер/индикаторы (RSI/EMA/ATR) пересчитаны из тейпа (5m), чтобы бэк был самодостаточным.
Сравниваем НАШ вход (FIRE) vs ПРАВИЛО (вето vol>3.4 + разворот отката) на максимуме истории.
"""
import sys, io, os, gzip, csv
from datetime import datetime
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import json
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
TAPE=r"E:\trading-data\ticks"
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")

COMM=0.10
LOOKBACK=8; RSI_OS=42; RSI_OB=58; EXT_MAX=0.004
VETO_VOL=3.40
DEF_STOP_K=1.5; DEF_TP_K=2.5
WARMUP_MS=6*3600*1000

def fnum(x):
    try: return float(x)
    except: return None

_cache={}
def day_ticks(sym,day):
    key=(sym,day)
    if key in _cache: return _cache[key]
    res=None
    for ext in (".csv.gz",".csv"):
        p=os.path.join(TAPE,sym,day+ext)
        if os.path.exists(p):
            op=gzip.open if ext==".csv.gz" else open
            ts=[];pr=[];sd=[]
            with op(p,"rt",encoding="utf-8",errors="ignore") as fh:
                r=csv.reader(fh); next(r,None)
                for row in r:
                    if len(row)<5: continue
                    try: t=int(row[0]); p=float(row[4]); sgn=1 if row[3]=="buy" else -1
                    except: continue
                    ts.append(t); pr.append(p); sd.append(sgn)
            res=(np.array(ts,np.int64),np.array(pr,np.float64),np.array(sd,np.int8)); break
    _cache[key]=res; return res

def window(sym,start,end):
    days=set()
    t=start-86400000
    while t<=end+86400000:
        days.add(datetime.utcfromtimestamp(t/1000).strftime("%Y-%m-%d")); t+=86400000
    TS=[];PR=[];SD=[]
    for d in sorted(days):
        a=day_ticks(sym,d)
        if a is None: continue
        TS.append(a[0]);PR.append(a[1]);SD.append(a[2])
    if not TS: return None
    ts=np.concatenate(TS);pr=np.concatenate(PR);sd=np.concatenate(SD)
    o=np.argsort(ts,kind="stable"); ts,pr,sd=ts[o],pr[o],sd[o]
    i0=np.searchsorted(ts,start); i1=np.searchsorted(ts,end,side="right")
    return ts[i0:i1],pr[i0:i1],sd[i0:i1]

def bars5(ts,pr):
    if len(ts)==0: return []
    bs=(ts//300000)*300000; out=[]; cur=None
    for k in range(len(ts)):
        b=int(bs[k]); p=float(pr[k])
        if cur is None or b!=cur["t"]:
            if cur is not None: out.append(cur)
            cur={"t":b,"o":p,"h":p,"l":p,"c":p}
        else:
            if p>cur["h"]:cur["h"]=p
            if p<cur["l"]:cur["l"]=p
            cur["c"]=p
    if cur is not None: out.append(cur)
    return out

def rsi_arr(c,period=14):
    n=len(c);out=[None]*n
    if n<period+1: return out
    g=l=0.0
    for i in range(1,period+1):
        ch=c[i]-c[i-1]; g+=max(ch,0); l+=max(-ch,0)
    ag=g/period; al=l/period
    out[period]=100-100/(1+ag/al) if al>0 else 100.0
    for i in range(period+1,n):
        ch=c[i]-c[i-1]; ag=(ag*(period-1)+max(ch,0))/period; al=(al*(period-1)+max(-ch,0))/period
        out[i]=100-100/(1+ag/al) if al>0 else 100.0
    return out

def ema_arr(c,period=20):
    n=len(c);out=[None]*n
    if n<period: return out
    k=2/(period+1); s=sum(c[:period])/period; out[period-1]=s
    for i in range(period,n):
        s=c[i]*k+s*(1-k); out[i]=s
    return out

def atr_at(bars,i,period=14):
    if i<1: return None
    trs=[max(bars[j]["h"]-bars[j]["l"],abs(bars[j]["h"]-bars[j-1]["c"]),abs(bars[j]["l"]-bars[j-1]["c"])) for j in range(max(1,i-period+1),i+1)]
    return sum(trs)/len(trs) if trs else None

def trig(bars,rsi,ema,t,sgn):
    if t<LOOKBACK: return False
    seg=rsi[t-LOOKBACK:t+1]
    if seg[-1] is None or seg[-2] is None: return False
    pulled=any(v is not None and (v<RSI_OS if sgn>0 else v>RSI_OB) for v in seg)
    o=bars[t]["o"];c=bars[t]["c"];cp=bars[t-1]["c"];e=ema[t]
    if e is None: return False
    turn=sgn*(c-cp)>0 and sgn*(c-o)>0
    not_ext=sgn*(c/e-1)<=EXT_MAX
    return pulled and turn and not_ext

def entry_fill(ts,pr,sd,dts,sgn):
    i=int(np.searchsorted(ts,dts))
    for k in range(i,min(i+5000,len(ts))):
        if sd[k]==sgn: return int(ts[k]),float(pr[k])
    if i<len(ts): return int(ts[i]),float(pr[i])
    return None,None

def sim_path(ts,pr,sd,e_ts,e_px,stop,tp,sgn,deadline):
    i=int(np.searchsorted(ts,e_ts,side="right"))
    last=e_px
    for k in range(i,len(ts)):
        if ts[k]>deadline: return "TIME",last
        p=float(pr[k]); last=p
        if (p<=stop) if sgn>0 else (p>=stop): return "SL",p        # market через пробивший принт (консерв.)
        if (p>=tp) if sgn>0 else (p<=tp): return "TP",tp           # limit по уровню
    return "TIME",last

def net(e,x,sgn): return sgn*(x-e)/e*100-COMM

def build():
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    out=[]; skipped=0
    for s in snaps:
        sym=s["symbol"]; sgn=1 if s["side"] in ("buy","long") else -1
        ts0=int(s["ts_ms"]); H=max(6,int(s.get("hold_min",75))//5); Hms=H*300000
        w=window(sym,ts0-WARMUP_MS,ts0+Hms+900000)
        if w is None or len(w[0])<200: skipped+=1; continue
        ts,pr,sd=w
        bars=bars5(ts,pr)
        if len(bars)<30: skipped+=1; continue
        closes=[b["c"] for b in bars]; rsi=rsi_arr(closes); ema=ema_arr(closes)
        bt=[b["t"] for b in bars]
        fire=int(np.searchsorted(np.array(bt),ts0,side="right"))-1
        if fire<LOOKBACK or fire>=len(bars)-2: skipped+=1; continue
        vol=fnum(s.get("context",{}).get("vol_ratio_sig"))
        a,b=max(LOOKBACK,fire-24),min(len(bars)-1,fire+H)
        rule_t=next((t for t in range(a,b) if trig(bars,rsi,ema,t,sgn)),None)
        out.append(dict(sym=sym.replace("-USDT-SWAP",""),sgn=sgn,H=H,Hms=Hms,fire=fire,vol=vol,
                        ts=ts,pr=pr,sd=sd,bars=bars,bt=bt,ts0=ts0,
                        regime=s.get("regime"),style=s.get("trade_style"),
                        day=datetime.utcfromtimestamp(ts0/1000).strftime("%Y-%m-%d")))
        out[-1]["rule_t"]=rule_t
    return out,skipped

def one(d,idx,stop_k,tp_k):
    """симуляция входа на баре idx → (outcome, net%)"""
    bars=d["bars"]; ts,pr,sd=d["ts"],d["pr"],d["sd"]; sgn=d["sgn"]
    dts=bars[idx]["t"]+300000                      # решение на закрытии бара
    e_ts,e_px=entry_fill(ts,pr,sd,dts,sgn)
    if e_px is None: return None
    a=atr_at(bars,idx)
    if not a or not e_px: return None
    stop=e_px-sgn*stop_k*a; tp=e_px+sgn*tp_k*a
    oc,xp=sim_path(ts,pr,sd,e_ts,e_px,stop,tp,sgn,dts+d["Hms"])
    return oc,net(e_px,xp,sgn)

def run(data,stop_k,tp_k,veto_vol=VETO_VOL):
    fn=[];rn=[];mf=[];mr=[];veto=[];notrig=[];oc=[];offs=[]
    for d in data:
        fr=one(d,d["fire"],stop_k,tp_k)
        if fr is None: continue
        fn.append(fr[1])
        if d["vol"] is not None and d["vol"]>veto_vol: veto.append(fr[1]); continue
        if d["rule_t"] is None: notrig.append(fr[1]); continue
        rr=one(d,d["rule_t"],stop_k,tp_k)
        if rr is None: continue
        rn.append(rr[1]); oc.append(rr[0]); mf.append(fr[1]); mr.append(rr[1]); offs.append((d["rule_t"]-d["fire"])*5)
    return dict(fn=fn,rn=rn,mf=mf,mr=mr,veto=veto,notrig=notrig,oc=oc,offs=offs)

def mean(a): return sum(a)/len(a) if a else float("nan")
def wr(a): return 100*sum(1 for x in a if x>0)/len(a) if a else float("nan")

def main():
    print("[1/2] строю на тейпе (тики→5m, индикаторы, триггер)...",flush=True)
    data,skipped=build()
    print(f"      сигналов в бэке: {len(data)} | пропущено (нет тейпа/мало тиков): {skipped}\n",flush=True)
    R=run(data,DEF_STOP_K,DEF_TP_K)
    from collections import Counter
    fn,rn=R["fn"],R["rn"]
    print(f"=== ПОЛНЫЙ БЭК НА ТЕЙПЕ | комса {COMM}%/круг | вход=реальный принт агрессора (спред в цене) ===")
    print(f"--- геометрия стоп {DEF_STOP_K}×ATR / тейк {DEF_TP_K}×ATR (R:R {DEF_TP_K/DEF_STOP_K:.1f}) ---")
    print(f"  НАШ ВХОД (FIRE):  сделок {len(fn):3}  ср.NET {mean(fn):+.2f}%  сумма {sum(fn):+.1f}%  WR {wr(fn):.0f}%")
    print(f"  ПРАВИЛО (RULE):   сделок {len(rn):3}  ср.NET {mean(rn):+.2f}%  сумма {sum(rn):+.1f}%  WR {wr(rn):.0f}%")
    print(f"     исходы RULE: {dict(Counter(R['oc']))}")
    print(f"\n  РАЗЛОЖЕНИЕ:")
    print(f"   • ВЕТО отсеяло {len(R['veto'])}: их FIRE-NET ср. {mean(R['veto']):+.2f}% (если <0 → спасло)")
    print(f"   • нет разворота, пропущено {len(R['notrig'])}: их FIRE-NET {mean(R['notrig']):+.2f}%")
    print(f"   • на ОДНИХ И ТЕХ ЖЕ {len(R['mf'])} сделках: FIRE {mean(R['mf']):+.2f}% → RULE {mean(R['mr']):+.2f}% (дельта входа {mean(R['mr'])-mean(R['mf']):+.2f}%)")
    print(f"     правило входило в среднем {mean(R['offs']):+.0f} мин относительно нас")
    print(f"\n--- РОБАСТНОСТЬ по геометрии (сумма NET% | ср.NET) ---")
    print(f"  {'стоп×тейк':>11} {'FIRE сум':>9} {'RULE сум':>9} {'FIRE ср':>8} {'RULE ср':>8} {'RULE WR':>8}")
    for sk in (1.0,1.5,2.0):
        for tk in (1.5,2.5,3.5):
            r=run(data,sk,tk)
            print(f"  {sk:>4.1f}×{tk:<4.1f} {sum(r['fn']):>9.1f} {sum(r['rn']):>9.1f} {mean(r['fn']):>8.2f} {mean(r['rn']):>8.2f} {wr(r['rn']):>7.0f}%")
    print(f"\n  ЧЕСТНО: вход рыночный (не лимитка бота); SL market консервативно по пробившему принту; TP limit;")
    print(f"  TIME market по последнему принту (спред на выходе аппроксимирован); привязка к нашим же событиям.")

if __name__=="__main__":
    main()
