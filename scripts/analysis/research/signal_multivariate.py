# -*- coding: utf-8 -*-
"""
signal_multivariate.py — МУЛЬТИВАРИАНТНЫЙ разбор: одиночный эффект vs частный (контроль остальных).

Отвечает на критику «ты сравниваешь по одному параметру». Для каждого параметра:
  - одиночная корреляция с захватом хода (fire_fav)  — как в простом разборе,
  - частный коэффициент в линейной модели на ВСЕХ параметрах сразу (контроль остальных),
  - выживает ли он, когда добавляем dummy режима/стиля (тень или причина).
Плюс: матрица корреляций (что дублирует что), CV-проверка (есть ли вообще сигнал вне шума). Read-only.
"""
import sys, io, os, gzip, json, csv
from datetime import datetime
import numpy as np
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import StandardScaler
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

NUM=["vol_ratio","adx_1h","adx_rising","day_pos","di_dir","dist_ema","bb_expanding"]

def build():
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
        ctx=s.get("context",{}); ind=s.get("indicators",{})
        e=fnum(s.get("entry")); e15=None
        try: e15=fnum(ind.get("15m",{}).get("ema20"))
        except: pass
        di=fnum(ctx.get("plus_di_1h")); dm=fnum(ctx.get("minus_di_1h"))
        data.append({
            "fire_fav":fav(fire),"regime":s.get("regime"),"style":s.get("trade_style"),
            "vol_ratio":fnum(ctx.get("vol_ratio_sig")),"adx_1h":fnum(ctx.get("adx_1h")),
            "adx_rising":1.0 if ctx.get("adx_1h_rising") is True else (0.0 if ctx.get("adx_1h_rising") is False else None),
            "day_pos":fnum(ctx.get("day_position")),
            "di_dir":(sgn*(di-dm)) if (di is not None and dm is not None) else None,
            "dist_ema":(-sgn*(e/e15-1)*100) if (e and e15) else None,
            "bb_expanding":1.0 if ctx.get("bb_expanding") is True else (0.0 if ctx.get("bb_expanding") is False else None),
        })
    return data

def main():
    data=build(); n=len(data)
    # missingness + median-impute
    print(f"=== МУЛЬТИВАРИАНТ: {n} сигналов ===\n")
    print("Пропуски по параметрам (импутируем медианой):")
    med={}
    for k in NUM:
        vals=[d[k] for d in data if d[k] is not None]
        med[k]=float(np.median(vals)) if vals else 0.0
        miss=sum(1 for d in data if d[k] is None)
        print(f"  {k:12} пропусков {miss:3}/{n}")
    X=np.array([[ (d[k] if d[k] is not None else med[k]) for k in NUM] for d in data],float)
    y=np.array([d["fire_fav"] for d in data],float)
    Xs=StandardScaler().fit_transform(X)
    ys=(y-y.mean())/ (y.std() or 1)

    # 1) корреляции между параметрами (что дублирует что)
    print("\n1) КОРРЕЛЯЦИИ МЕЖДУ ПАРАМЕТРАМИ (|r|>0.4 = дублируют друг друга):")
    C=np.corrcoef(Xs.T)
    for i in range(len(NUM)):
        for j in range(i+1,len(NUM)):
            if abs(C[i,j])>=0.4:
                print(f"   {NUM[i]:12} ~ {NUM[j]:12} r={C[i,j]:+.2f}")

    # 2) одиночный vs частный эффект на захват
    print("\n2) ОДИНОЧНЫЙ vs ЧАСТНЫЙ эффект на захват хода (fire_fav):")
    print(f"   {'параметр':12}{'одиночн r':>11}{'частный β':>11}  вердикт")
    lin=LinearRegression().fit(Xs,ys); beta=lin.coef_
    for idx,k in enumerate(NUM):
        uni=np.corrcoef(Xs[:,idx],ys)[0,1]
        survive = (abs(beta[idx])>=0.12 and np.sign(beta[idx])==np.sign(uni))
        weak = abs(uni)<0.12
        verdict = ("СЛАБ оба" if weak and abs(beta[idx])<0.12 else
                   ("выживает" if survive else "СХЛОПЫВАЕТСЯ (тень)"))
        print(f"   {k:12}{uni:+11.2f}{beta[idx]:+11.2f}  {verdict}")
    print(f"   R^2 модели (in-sample): {lin.score(Xs,ys):.2f}")

    # 3) добавляем dummy режима/стиля — выживает ли vol_ratio
    regs=sorted({d['regime'] for d in data}); sts=sorted({d['style'] for d in data})
    D=[]
    for d in data:
        row=[ (d[k] if d[k] is not None else med[k]) for k in NUM]
        row+=[1.0 if d['regime']==r else 0.0 for r in regs[1:]]
        row+=[1.0 if d['style']==st else 0.0 for st in sts[1:]]
        D.append(row)
    Dn=["+".join([k]) for k in NUM]+[f"reg={r}" for r in regs[1:]]+[f"sty={st}" for st in sts[1:]]
    Ds=StandardScaler().fit_transform(np.array(D,float))
    lin2=LinearRegression().fit(Ds,ys); b2=lin2.coef_
    print("\n3) С КОНТРОЛЕМ РЕЖИМА/СТИЛЯ (выживает ли эффект параметра):")
    print(f"   {'параметр':14}{'β без контр.':>13}{'β с контр.':>12}")
    for idx,k in enumerate(NUM):
        print(f"   {k:14}{beta[idx]:+13.2f}{b2[idx]:+12.2f}")
    print(f"   режимы/стиль: " + ", ".join(f"{Dn[len(NUM)+i]}={b2[len(NUM)+i]:+.2f}" for i in range(len(Dn)-len(NUM))))
    print(f"   R^2 с контролем: {lin2.score(Ds,ys):.2f}")

    # 4) честная CV — есть ли вообще сигнал вне шума
    print("\n4) ЕСТЬ ЛИ СИГНАЛ ВНЕ ШУМА (worker/dud, 5-fold CV, базовая линия 0.50):")
    yb=(y>=np.median(y)).astype(int)
    log=LogisticRegression(max_iter=1000)
    acc=cross_val_score(log,Xs,yb,cv=5,scoring="accuracy").mean()
    print(f"   логрег на всех параметрах: CV-accuracy = {acc:.2f}")
    tree=DecisionTreeClassifier(max_depth=2,min_samples_leaf=12,random_state=0)
    acct=cross_val_score(tree,X,yb,cv=5,scoring="accuracy").mean()
    print(f"   дерево глубины 2:          CV-accuracy = {acct:.2f}")
    tree.fit(X,yb)
    print("\n   КОМБИНИРОВАННОЕ ПРАВИЛО (дерево, читаемое):")
    print(export_text(tree,feature_names=NUM,show_weights=True))

if __name__=="__main__":
    main()
