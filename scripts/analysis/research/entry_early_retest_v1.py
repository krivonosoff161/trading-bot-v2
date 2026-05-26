# -*- coding: utf-8 -*-
"""
entry_early_retest_v1.py — решающий NET-тест кандидата GPT EARLY_RETEST_EXHAUSTION_V1. Read-only.

Триггер (причинный, спека `docs/gpt_early_entry_findings_2026-05-26.md`): внутри уже выбранного направления
ищем РАННИЙ вход = откат к EMA + RSI-истощение + НЕ climax по объёму + не растянут + первый разворотный бар.
Считаем NET (не MFE) vs наш FIRE на ДВУХ периодах:
  • МАЙ  — тик-точный тейп (E:\\trading-data\\ticks), реальные принты агрессора.
  • АПРЕЛЬ— 5m-свечи OKX (history-candles), стоп-первым консервативно.
Протокол отбраковки GPT: отклонить, если NET<=0 в любом периоде, или matched+ но абсолют- в ОБОИХ.
Вход рыночный у обоих (FIRE и EARLY), геометрия и комиссия одинаковы внутри периода.
"""
import sys, io, os, json, gzip, csv, time, urllib.request
from datetime import datetime
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
ARCH=os.path.join(ROOT,"logs_archive","09.05.2026","signals","signal_log.jsonl")
TAPE=r"E:\trading-data\ticks"

STOP_K=1.5; TP_K=2.5; WARMUP=6*3600*1000
# EARLY trigger params (GPT spec)
PB_LO,PB_HI=0.15,2.50; RSI_EX=2.0; V5_LO,V5_HI=0.45,2.20; CTXV_MAX=3.40; RR_MAX=1.35; EXT_MAX=0.40

def fnum(x):
    try: return float(x)
    except: return None

# ---------- indicators ----------
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
def vol_ratio_at(b,i,p=15):
    if i<1: return None
    pv=[b[j]["v"] for j in range(max(0,i-p),i) if b[j]["v"] is not None]
    if not pv or sum(pv)==0: return None
    m=sum(pv)/len(pv)
    return b[i]["v"]/m if (m>0 and b[i]["v"] is not None) else None
def range_ratio_at(b,i,p=12):
    if i<1: return None
    cur=(b[i]["h"]-b[i]["l"])/b[i]["c"]*100 if b[i]["c"] else None
    pr=[(b[j]["h"]-b[j]["l"])/b[j]["c"]*100 for j in range(max(0,i-p),i) if b[j]["c"]]
    if cur is None or not pr: return None
    m=sum(pr)/len(pr)
    return cur/m if m>0 else None
def day_pos_at(b,i):
    """причинно: позиция close в диапазоне дня (по барам с UTC-полуночи до i)."""
    day0=(b[i]["t"]//86400000)*86400000
    hs=[b[j]["h"] for j in range(0,i+1) if b[j]["t"]>=day0]
    ls=[b[j]["l"] for j in range(0,i+1) if b[j]["t"]>=day0]
    if not hs: return None
    hi=max(hs); lo=min(ls)
    return (b[i]["c"]-lo)/(hi-lo) if hi>lo else 0.5

def early_trigger(b,rsi,ema,i,sgn,ctx_vol):
    if i<1: return False
    c=b[i]["c"]; o=b[i]["o"]; cp=b[i-1]["c"]; e=ema[i]; r=rsi[i]
    if None in (c,o,cp,e,r) or e==0: return False
    pull=-sgn*(c/e-1)*100
    if not (PB_LO<=pull<=PB_HI): return False
    rex=(50-r) if sgn>0 else (r-50)
    if rex<RSI_EX: return False
    v5=vol_ratio_at(b,i)
    if v5 is None or not (V5_LO<=v5<=V5_HI): return False
    if ctx_vol is not None and ctx_vol>CTXV_MAX: return False
    rr=range_ratio_at(b,i)
    if rr is None or rr>RR_MAX: return False
    if not ((sgn*(c-o)>0) or (sgn*(c-cp)>0)): return False
    if sgn*(c/e-1)*100>EXT_MAX: return False
    dp=day_pos_at(b,i)
    if dp is not None and ((sgn>0 and dp>0.90) or (sgn<0 and dp<0.10)): return False
    return True

# ---------- MAY: tape ----------
_tc={}
def day_ticks(sym,day):
    k=(sym,day)
    if k in _tc: return _tc[k]
    res=None
    p=os.path.join(TAPE,sym,day+".csv.gz"); p2=os.path.join(TAPE,sym,day+".csv")
    fp=p if os.path.exists(p) else (p2 if os.path.exists(p2) else None)
    if fp:
        ts=[];pr=[];sd=[];sz=[]
        op=gzip.open if fp.endswith(".gz") else open
        with op(fp,"rt",encoding="utf-8",errors="ignore") as fh:
            r=csv.reader(fh); next(r,None)
            for row in r:
                if len(row)<6: continue
                try: t=int(row[0]); p_=float(row[4]); s=1 if row[3]=="buy" else -1; z=float(row[5])
                except: continue
                ts.append(t);pr.append(p_);sd.append(s);sz.append(z)
        res=(np.array(ts,np.int64),np.array(pr,np.float64),np.array(sd,np.int8),np.array(sz,np.float64))
    _tc[k]=res; return res
def tape_window(sym,start,end):
    days=set(); t=start-86400000
    while t<=end+86400000: days.add(datetime.utcfromtimestamp(t/1000).strftime("%Y-%m-%d")); t+=86400000
    TS=[];PR=[];SD=[];SZ=[]
    for d in sorted(days):
        a=day_ticks(sym,d)
        if a is None: continue
        TS.append(a[0]);PR.append(a[1]);SD.append(a[2]);SZ.append(a[3])
    if not TS: return None
    ts=np.concatenate(TS);pr=np.concatenate(PR);sd=np.concatenate(SD);sz=np.concatenate(SZ)
    o=np.argsort(ts,kind="stable"); ts,pr,sd,sz=ts[o],pr[o],sd[o],sz[o]
    i0=np.searchsorted(ts,start); i1=np.searchsorted(ts,end,side="right")
    return ts[i0:i1],pr[i0:i1],sd[i0:i1],sz[i0:i1]
def bars_from_ticks(ts,pr,sz):
    if len(ts)==0: return []
    bs=(ts//300000)*300000; out=[]; cur=None
    for k in range(len(ts)):
        bk=int(bs[k]); p=float(pr[k]); z=float(sz[k])
        if cur is None or bk!=cur["t"]:
            if cur is not None: out.append(cur)
            cur={"t":bk,"o":p,"h":p,"l":p,"c":p,"v":z}
        else:
            if p>cur["h"]:cur["h"]=p
            if p<cur["l"]:cur["l"]=p
            cur["c"]=p; cur["v"]+=z
    if cur is not None: out.append(cur)
    return out
def tape_entry_fill(ts,pr,sd,dts,sgn):
    i=int(np.searchsorted(ts,dts))
    for k in range(i,min(i+5000,len(ts))):
        if sd[k]==sgn: return int(ts[k]),float(pr[k])
    if i<len(ts): return int(ts[i]),float(pr[i])
    return None,None
def tape_sim(ts,pr,e_ts,e_px,stop,tp,sgn,deadline,comm):
    i=int(np.searchsorted(ts,e_ts,side="right")); last=e_px
    for k in range(i,len(ts)):
        if ts[k]>deadline: return sgn*(last-e_px)/e_px*100-comm
        p=float(pr[k]); last=p
        if (p<=stop) if sgn>0 else (p>=stop): return sgn*(p-e_px)/e_px*100-comm
        if (p>=tp) if sgn>0 else (p<=tp): return sgn*(tp-e_px)/e_px*100-comm
    return sgn*(last-e_px)/e_px*100-comm

# ---------- APRIL: OKX candles ----------
_oc={}
def fetch_5m(inst,start,end):
    c=_oc.setdefault(inst,{})
    if c and min(c)<=start and max(c)>=end: return
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
        for row in data: c[int(row[0])]=(float(row[1]),float(row[2]),float(row[3]),float(row[4]),float(row[5]))
        old=min(int(row[0]) for row in data)
        if old>=cursor: break
        cursor=old; time.sleep(0.12)
def okx_bars(inst,start,end):
    c=_oc.get(inst,{})
    return [dict(t=t,o=v[0],h=v[1],l=v[2],c=v[3],v=v[4]) for t,v in sorted(c.items()) if start<=t<=end]
def candle_sim(b,idx,sgn,H,comm,slip):
    e=b[idx]["c"]; a=atr_at(b,idx)
    if not e or not a: return None
    e=e*(1+sgn*slip)
    stop=e-sgn*STOP_K*a; tp=e+sgn*TP_K*a
    for j in range(idx+1,min(idx+H+1,len(b))):
        adv=b[j]["l"] if sgn>0 else b[j]["h"]; fav=b[j]["h"] if sgn>0 else b[j]["l"]
        if (adv<=stop) if sgn>0 else (adv>=stop): return sgn*(stop-e)/e*100-comm
        if (fav>=tp) if sgn>0 else (fav<=tp): return sgn*(tp-e)/e*100-comm
    last=b[min(idx+H,len(b)-1)]["c"]
    return sgn*(last-e)/e*100-comm

# ---------- drivers ----------
def mean(a): return sum(a)/len(a) if a else float("nan")
def wr(a): return 100*sum(1 for x in a if x>0)/len(a) if a else float("nan")

def run_may():
    sigs=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    out=[]
    for s in sigs:
        reg=s.get("regime")
        if reg not in ("DRIFT","TRENDING"): continue   # V1: без RANGING
        sym=s["symbol"]; sgn=1 if s["side"] in("buy","long") else -1
        ts0=int(s["ts_ms"]); H=max(6,int(s.get("hold_min",75))//5); Hms=H*300000
        w=tape_window(sym,ts0-WARMUP,ts0+Hms+900000)
        if w is None or len(w[0])<200: continue
        ts,pr,sd,sz=w; b=bars_from_ticks(ts,pr,sz)
        if len(b)<30: continue
        cl=[x["c"] for x in b]; rsi=rsi_arr(cl); ema=ema_arr(cl); bt=[x["t"] for x in b]
        fire=int(np.searchsorted(np.array(bt),ts0,side="right"))-1
        if fire<14 or fire>=len(b)-2: continue
        ctxv=fnum(s.get("context",{}).get("vol_ratio_sig"))
        early=next((t for t in range(max(14,fire-24),fire+1) if early_trigger(b,rsi,ema,t,sgn,ctxv)),None)
        def net_at(idx):
            a=atr_at(b,idx)
            if not a: return None
            dts=b[idx]["t"]+300000
            e_ts,e_px=tape_entry_fill(ts,pr,sd,dts,sgn)
            if e_px is None: return None
            return tape_sim(ts,pr,e_ts,e_px,e_px-sgn*STOP_K*a,e_px+sgn*TP_K*a,sgn,dts+Hms,0.10)
        fn=net_at(fire)
        if fn is None: continue
        en=net_at(early) if early is not None else None
        out.append(dict(reg=reg,fire=fn,early=en,trig=early is not None))
    return out

def run_apr():
    sigs=[]
    for l in open(ARCH,encoding="utf-8"):
        if not l.strip(): continue
        r=json.loads(l); sym=r.get("symbol"); ts=r.get("ts_ms"); side=r.get("side"); reg=r.get("regime")
        if not(sym and ts and side and reg) or reg not in("DRIFT","TRENDING"): continue
        sigs.append((sym if sym.endswith("-SWAP") else sym+"-SWAP",int(ts),
                     1 if side in("buy","long") else -1,reg,r.get("vol_ratio"),
                     max(6,int(r.get("max_hold_min",90))//5)))
    out=[]; done=0
    for inst,ts0,sgn,reg,ctxv,H in sigs:
        fetch_5m(inst,ts0-WARMUP,ts0+H*300000+1800000)
        b=okx_bars(inst,ts0-WARMUP,ts0+H*300000+1800000)
        if len(b)<30: continue
        cl=[x["c"] for x in b]; rsi=rsi_arr(cl); ema=ema_arr(cl); bt=[x["t"] for x in b]
        fire=None
        for i in range(len(bt)):
            if bt[i]<=ts0: fire=i
            else: break
        if fire is None or fire<14 or fire>=len(b)-2: continue
        early=next((t for t in range(max(14,fire-24),fire+1) if early_trigger(b,rsi,ema,t,sgn,ctxv)),None)
        fn=candle_sim(b,fire,sgn,H,0.20,sgn*0.001)
        if fn is None: continue
        en=candle_sim(b,early,sgn,H,0.20,sgn*0.001) if early is not None else None
        out.append(dict(reg=reg,fire=fn,early=en,trig=early is not None))
        done+=1
        if done%40==0: print(f"   ...апрель {done}",flush=True)
    return out

def report(name,data):
    fire=[d["fire"] for d in data]
    early=[d["early"] for d in data if d["trig"] and d["early"] is not None]
    matched=[(d["fire"],d["early"]) for d in data if d["trig"] and d["early"] is not None]
    print(f"\n=== {name} (сигналов {len(data)}, DRIFT+TRENDING) ===")
    print(f"  FIRE:  ср.NET {mean(fire):+.2f}%  сумма {sum(fire):+.1f}%  WR {wr(fire):.0f}%  n={len(fire)}")
    print(f"  EARLY: ср.NET {mean(early):+.2f}%  сумма {sum(early):+.1f}%  WR {wr(early):.0f}%  сделок {len(early)}")
    if matched:
        mf=[m[0] for m in matched]; me=[m[1] for m in matched]
        print(f"  matched ({len(matched)}): FIRE {mean(mf):+.2f}% → EARLY {mean(me):+.2f}% (дельта {mean(me)-mean(mf):+.2f}%)")
    for rg in ("DRIFT","TRENDING"):
        sub=[d for d in data if d["reg"]==rg]
        e=[d["early"] for d in sub if d["trig"] and d["early"] is not None]
        f=[d["fire"] for d in sub]
        print(f"    {rg:9} FIRE {mean(f):+.2f}% | EARLY {mean(e):+.2f}% (n_early {len(e)})")
    return mean(early) if early else float("nan"), (mean([m[1] for m in matched])-mean([m[0] for m in matched]) if matched else float("nan"))

def main():
    print("[МАЙ] тейп...",flush=True); may=run_may()
    print("[АПРЕЛЬ] OKX свечи...",flush=True); apr=run_apr()
    print("\n"+"="*70)
    print("EARLY_RETEST_EXHAUSTION_V1 — NET на двух периодах (геом. 1.5/2.5 ATR)")
    print("="*70)
    me_net,me_delta=report("МАЙ (тик-тейп, комса 0.10%)",may)
    ap_net,ap_delta=report("АПРЕЛЬ (5m OKX, комса 0.20%)",apr)
    print("\n--- ПРОТОКОЛ ОТБРАКОВКИ GPT ---")
    fail=[]
    if not (me_net>0): fail.append("МАЙ NET<=0")
    if not (ap_net>0): fail.append("АПРЕЛЬ NET<=0")
    if (me_delta>0 and ap_delta>0) and not (me_net>0 and ap_net>0):
        fail.append("matched+ но абсолют- в обоих → как прошлое правило")
    if fail:
        print("  ❌ ОТКЛОНИТЬ:", "; ".join(fail))
        print("  (относит. дельта может быть +, но это снова 'менее плохо', не деньги)")
    else:
        print("  ✅ ПРОШЁЛ оба периода NET+ → реальный кандидат в paper-движок (нужен GO трейдера)")

if __name__=="__main__":
    main()
