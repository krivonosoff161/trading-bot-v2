# -*- coding: utf-8 -*-
"""
entry_archive_backtest.py — НЕЗАВИСИМЫЙ OOS на АРХИВЕ (апрель–нач.мая, до тейпа). Read-only.

Архивные сигналы бота (logs_archive/09.05.2026/signals/signal_log.jsonl, 9.04–8.05, 170 шт, 102 DRIFT)
торговались ДО тейпа → филлы из исторических 5m-свечей OKX (history-candles). Это другой период И другой
конфиг скринера → жёсткая проверка устойчивости эджа тайминга, особенно в DRIFT.
Комса 0.20%/круг (консервативнее майской 0.10% — спред/слип без тиков закладываем сюда).
"""
import sys, io, os, json, time, urllib.request
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ARCH=os.path.join(ROOT,"logs_archive","09.05.2026","signals","signal_log.jsonl")

COMM=0.20
LOOKBACK=8; RSI_OS=42; RSI_OB=58; EXT_MAX=0.004
VETO_VOL=3.40
DEF_STOP_K=1.5; DEF_TP_K=2.5
WARMUP_MS=6*3600*1000

_cache={}  # inst -> {bar_ts: (o,h,l,c)}
def fetch_5m(inst,start,end):
    c=_cache.setdefault(inst,{})
    need_lo=min(c.keys()) if c else None; need_hi=max(c.keys()) if c else None
    if c and need_lo<=start and need_hi>=end: return   # уже покрыт
    cursor=end+300000
    for _ in range(40):
        if cursor<=start: break
        url=f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=5m&after={cursor}&limit=100"
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=15) as r: j=json.loads(r.read())
        except Exception: break
        data=j.get("data",[])
        if not data: break
        for row in data:
            t=int(row[0]); c[t]=(float(row[1]),float(row[2]),float(row[3]),float(row[4]))
        oldest=min(int(row[0]) for row in data)
        if oldest>=cursor: break
        cursor=oldest
        time.sleep(0.12)

def bars_in(inst,start,end):
    c=_cache.get(inst,{})
    return [dict(t=t,o=v[0],h=v[1],l=v[2],c=v[3]) for t,v in sorted(c.items()) if start<=t<=end]

def rsi_arr(c,p=14):
    n=len(c);o=[None]*n
    if n<p+1: return o
    g=l=0.0
    for i in range(1,p+1): ch=c[i]-c[i-1]; g+=max(ch,0); l+=max(-ch,0)
    ag=g/p; al=l/p; o[p]=100-100/(1+ag/al) if al>0 else 100.0
    for i in range(p+1,n):
        ch=c[i]-c[i-1]; ag=(ag*(p-1)+max(ch,0))/p; al=(al*(p-1)+max(-ch,0))/p
        o[i]=100-100/(1+ag/al) if al>0 else 100.0
    return o
def ema_arr(c,p=20):
    n=len(c);o=[None]*n
    if n<p: return o
    k=2/(p+1); s=sum(c[:p])/p; o[p-1]=s
    for i in range(p,n): s=c[i]*k+s*(1-k); o[i]=s
    return o
def atr_at(b,i,p=14):
    if i<1: return None
    tr=[max(b[j]["h"]-b[j]["l"],abs(b[j]["h"]-b[j-1]["c"]),abs(b[j]["l"]-b[j-1]["c"])) for j in range(max(1,i-p+1),i+1)]
    return sum(tr)/len(tr) if tr else None
def trig(b,rsi,ema,t,sgn):
    if t<LOOKBACK: return False
    seg=rsi[t-LOOKBACK:t+1]
    if seg[-1] is None or seg[-2] is None: return False
    pulled=any(v is not None and (v<RSI_OS if sgn>0 else v>RSI_OB) for v in seg)
    o=b[t]["o"];cc=b[t]["c"];cp=b[t-1]["c"];e=ema[t]
    if e is None: return False
    return pulled and (sgn*(cc-cp)>0 and sgn*(cc-o)>0) and (sgn*(cc/e-1)<=EXT_MAX)

def sim(b,idx,sgn,H,stop_k,tp_k):
    e=b[idx]["c"]; a=atr_at(b,idx)
    if not e or not a: return None
    e=e*(1+sgn*COMM/200)  # слиппедж входа ~полкомсы в худшую сторону
    stop=e-sgn*stop_k*a; tp=e+sgn*tp_k*a
    for j in range(idx+1,min(idx+H+1,len(b))):
        adv=b[j]["l"] if sgn>0 else b[j]["h"]; fav=b[j]["h"] if sgn>0 else b[j]["l"]
        hs=(adv<=stop) if sgn>0 else (adv>=stop); ht=(fav>=tp) if sgn>0 else (fav<=tp)
        if hs: return "SL",sgn*(stop-e)/e*100-COMM     # стоп первым (консерв., 5m без пути)
        if ht: return "TP",sgn*(tp-e)/e*100-COMM
    last=b[min(idx+H,len(b)-1)]["c"]
    return "TIME",sgn*(last-e)/e*100-COMM

def load_sigs():
    out=[]
    for l in open(ARCH,encoding="utf-8"):
        l=l.strip()
        if not l: continue
        r=json.loads(l)
        sym=r.get("symbol"); ts=r.get("ts_ms"); side=r.get("side"); reg=r.get("regime")
        if not(sym and ts and side and reg): continue
        inst=sym if sym.endswith("-SWAP") else sym+"-SWAP"
        out.append(dict(inst=inst,ts=int(ts),sgn=1 if side in("buy","long") else -1,
                        regime=reg,vol=r.get("vol_ratio"),H=max(6,int(r.get("max_hold_min",90))//5)))
    return out

def run(data,stop_k,tp_k,veto=VETO_VOL):
    fn=[];rn=[];mf=[];mr=[];veto_s=[];notr=[]
    for d in data:
        b=d["bars"]; fire=d["fire"]
        if fire is None: continue
        fr=sim(b,fire,d["sgn"],d["H"],stop_k,tp_k)
        if fr is None: continue
        fn.append(fr[1])
        if d["vol"] is not None and d["vol"]>veto: veto_s.append(fr[1]); continue
        if d["rule_t"] is None: notr.append(fr[1]); continue
        rr=sim(b,d["rule_t"],d["sgn"],d["H"],stop_k,tp_k)
        if rr is None: continue
        rn.append(rr[1]); mf.append(fr[1]); mr.append(rr[1])
    return dict(fn=fn,rn=rn,mf=mf,mr=mr,veto=veto_s,notr=notr)
def mean(a): return sum(a)/len(a) if a else float("nan")
def wr(a): return 100*sum(1 for x in a if x>0)/len(a) if a else float("nan")

def main():
    sigs=load_sigs()
    print(f"[1/2] архивных сигналов: {len(sigs)} | тяну 5m-свечи OKX (апрель)...",flush=True)
    data=[]; done=0; failsym=set()
    for s in sigs:
        ws,we=s["ts"]-WARMUP_MS, s["ts"]+s["H"]*300000+1800000
        fetch_5m(s["inst"],ws,we)
        b=bars_in(s["inst"],ws,we)
        if len(b)<30: failsym.add(s["inst"]); continue
        closes=[x["c"] for x in b]; rsi=rsi_arr(closes); ema=ema_arr(closes)
        bt=[x["t"] for x in b]
        fire=None
        for i in range(len(bt)):
            if bt[i]<=s["ts"]: fire=i
            else: break
        if fire is None or fire<LOOKBACK or fire>=len(b)-2: continue
        a,bend=max(LOOKBACK,fire-24),min(len(b)-1,fire+s["H"])
        rule_t=next((t for t in range(a,bend) if trig(b,rsi,ema,t,s["sgn"])),None)
        data.append({**s,"bars":b,"fire":fire,"rule_t":rule_t})
        done+=1
        if done%30==0: print(f"   ...{done} готово",flush=True)
    print(f"      в бэке: {len(data)} | без свечей OKX: {len(failsym)} инстр.\n",flush=True)

    from collections import Counter
    R=run(data,DEF_STOP_K,DEF_TP_K)
    print(f"=== АРХИВ-OOS (апрель, 5m-филлы OKX, комса {COMM}%/круг) — стоп {DEF_STOP_K}×ATR / тейк {DEF_TP_K}×ATR ===")
    print(f"  FIRE:  сделок {len(R['fn']):3}  ср.NET {mean(R['fn']):+.2f}%  сумма {sum(R['fn']):+.1f}%  WR {wr(R['fn']):.0f}%")
    print(f"  RULE:  сделок {len(R['rn']):3}  ср.NET {mean(R['rn']):+.2f}%  сумма {sum(R['rn']):+.1f}%  WR {wr(R['rn']):.0f}%")
    print(f"  matched ({len(R['mf'])}): FIRE {mean(R['mf']):+.2f}% → RULE {mean(R['mr']):+.2f}% (дельта {mean(R['mr'])-mean(R['mf']):+.2f}%)")
    print(f"\n  *** ПО РЕЖИМАМ (главное — DRIFT) ***")
    for rg in sorted({d["regime"] for d in data},key=lambda r:-sum(1 for d in data if d["regime"]==r)):
        sub=[d for d in data if d["regime"]==rg]
        r=run(sub,DEF_STOP_K,DEF_TP_K)
        print(f"   {str(rg):9} n={len(sub):3} | FIRE {mean(r['fn']):+6.2f}% WR{wr(r['fn']):3.0f}%  ||  RULE {mean(r['rn']):+6.2f}% (сум {sum(r['rn']):+6.1f}) WR{wr(r['rn']):3.0f}% сделок {len(r['rn']):2}")
    print(f"\n  СРАВНЕНИЕ С МАЙ-ТЕЙПОМ: там DRIFT RULE +1.01%, TRENDING ~0, RANGING минус. Держится ли паттерн?")
    print(f"  ЧЕСТНО: другой конфиг скринера (апрель), 5m-филлы (грубее тика), комса выше — это проба УСТОЙЧИВОСТИ.")

if __name__=="__main__":
    main()
