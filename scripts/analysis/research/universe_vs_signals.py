# -*- coding: utf-8 -*-
"""
universe_vs_signals.py — а наш АНАЛИЗ вообще наводит на хорошие ходы? (read-only)

Вопрос трейдера: даже если входить раньше — может, мы вообще смотрим не туда и не видим КРУПНЫЕ чистые
движения? Сканируем ВСЮ вселенную (logs/features/5m, 57 символов) на лучший форвардный ход за 2ч в каждый
момент, находим крупные ходы, и сверяем: стрелял ли бот по ним (символ+день+сторона рядом со стартом).
Сравниваем размер хода на сигналах бота vs доступный во вселенной. Показываем крупные ходы, что бот не увидел.
"""
import sys, io, os, gzip, csv, json
from datetime import datetime, timedelta
from statistics import mean, median
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
F5=os.path.join(ROOT,"logs","features","5m")
SNAP=os.path.join(ROOT,"logs","signals","signal_snapshot.jsonl")
W=24            # форвардное окно = 2 часа (24×5m)
START="2026-05-11"; END="2026-05-25"

def fnum(x):
    try: return float(x)
    except: return None
def load_day(sym,day):
    for ext,op in ((".csv.gz",gzip.open),(".csv",open)):
        p=os.path.join(F5,sym,day+ext)
        if os.path.exists(p):
            with op(p,"rt",encoding="utf-8",errors="ignore") as fh: return list(csv.DictReader(fh))
    return []

def best_move(rows):
    """лучший форвардный ход (за W баров) по символу-дню: (start_ts, dir, size%)."""
    cl=[fnum(r.get("close")) for r in rows]; hi=[fnum(r.get("high")) for r in rows]; lo=[fnum(r.get("low")) for r in rows]
    ts=[int(r["ts_ms"]) for r in rows]
    bu=0.0; bdir=None; bts=None
    for i in range(len(rows)):
        if not cl[i]: continue
        up=dn=0.0
        for j in range(i+1,min(i+W+1,len(rows))):
            if hi[j]: up=max(up,(hi[j]-cl[i])/cl[i]*100)
            if lo[j]: dn=max(dn,(cl[i]-lo[j])/cl[i]*100)
        if up>bu: bu=up;bdir="buy";bts=ts[i]
        if dn>bu: bu=dn;bdir="sell";bts=ts[i]
    return bts,bdir,bu

def main():
    days=[]
    d0=datetime.strptime(START,"%Y-%m-%d"); d1=datetime.strptime(END,"%Y-%m-%d")
    dd=d0
    while dd<=d1: days.append(dd.strftime("%Y-%m-%d")); dd=dd+timedelta(days=1)
    syms=sorted(os.listdir(F5))
    # лучшая возможность по каждому символу-дню
    universe=[]   # (sym, day, start_ts, dir, size)
    for s in syms:
        for day in days:
            rows=load_day(s,day)
            if len(rows)<30: continue
            bts,bdir,sz=best_move(rows)
            if bts: universe.append((s,day,bts,bdir,sz))
    # сигналы бота
    snaps=[json.loads(x) for x in open(SNAP,encoding="utf-8") if x.strip() and json.loads(x).get("entry_signal")=="ENTRY"]
    sig_by_symday={}
    for sg in snaps:
        day=datetime.utcfromtimestamp(int(sg["ts_ms"])/1000).strftime("%Y-%m-%d")
        sig_by_symday.setdefault((sg["symbol"],day),[]).append((int(sg["ts_ms"]),sg["side"]))

    def caught(sym,day,start_ts,bdir):
        for ts,side in sig_by_symday.get((sym,day),[]):
            if side==bdir and (start_ts-60*60000)<=ts<=(start_ts+30*60000): return True
        return False

    print(f"=== ВСЕЛЕННАЯ {START}..{END}: {len(syms)} символов, {len(universe)} символо-дней с данными ===")
    print(f"(лучший форвардный ход за 2ч в каждый момент; «поймал» = бот стрелял той же стороной в окне старта)\n")
    for thr in (3,5,8,12):
        big=[u for u in universe if u[4]>=thr]
        got=[u for u in big if caught(*u[:4])]
        anyday=[u for u in big if (u[0],u[1]) in sig_by_symday]
        print(f"  ходы >= {thr:2}%:  {len(big):3} возможностей  |  бот стрелял ВОВРЕМЯ по {len(got):2} ({100*len(got)//max(1,len(big))}%)  |  хотя бы в тот день по символу: {len(anyday)}")

    # размер хода: у бота vs во вселенной
    bot_best=[]   # лучший ход в день по символам, где бот стрелял (его сторона)
    for (sym,day),sigs in sig_by_symday.items():
        rows=load_day(sym,day)
        if len(rows)<30: continue
        _,_,sz=best_move(rows)
        bot_best.append(sz)
    uni_sizes=[u[4] for u in universe]
    print(f"\n  РАЗМЕР ЛУЧШЕГО ХОДА (медиана / средн):")
    print(f"    вся вселенная (символо-дни):  {median(uni_sizes):.2f}% / {mean(uni_sizes):.2f}%")
    print(f"    дни-символы где бот торговал:  {median(bot_best):.2f}% / {mean(bot_best):.2f}%")
    # топ крупнейших ходов и поймал ли бот
    print(f"\n  ТОП-15 КРУПНЕЙШИХ ХОДОВ ВСЕЛЕННОЙ — поймал ли бот:")
    for u in sorted(universe,key=lambda u:-u[4])[:15]:
        sym,day,bts,bdir,sz=u
        c="✓ поймал" if caught(sym,day,bts,bdir) else ("• стрелял в тот день, но мимо" if (sym,day) in sig_by_symday else "✗ НЕ ВИДЕЛ")
        t=datetime.utcfromtimestamp(bts/1000).strftime("%m-%d %H:%M")
        print(f"    {sym.replace('-USDT-SWAP',''):8} {t} {bdir:4} {sz:5.1f}%   {c}")

if __name__=="__main__":
    main()
