"""Deep signal analysis with correct field names."""
import json

labels  = [json.loads(l) for l in open('logs/signals/main_signals_labels.jsonl') if l.strip()]
signals = [json.loads(l) for l in open('logs/signals/main_signals.jsonl') if l.strip()]
sig_map = {s.get('id') or s.get('signal_id'): s for s in signals}

rows = []
for lab in labels:
    sig = sig_map.get(lab['signal_id'], {})
    rows.append({
        'vol':    float(sig.get('vol_ratio') or 1),
        'adx':    float(sig.get('adx_1h') or 0),
        'fvg':    bool(sig.get('fvg_confirmed')),
        'win':    lab['outcome'] in ('TP1', 'TP2'),
        'sl':     lab['outcome'] == 'SL',
        'time':   lab['outcome'] == 'TIME',
        'regime': lab['regime'],
        'side':   sig.get('side', '?'),
        'ts':     lab['ts'],
        'symbol': lab['symbol'],
    })

excl = [r for r in rows if not r['time']]
wins_l = [r for r in excl if r['win']]
sls_l  = [r for r in excl if r['sl']]

print("=" * 50)
print(f"TOTAL: {len(rows)} | TP={sum(r['win'] for r in rows)} "
      f"SL={sum(r['sl'] for r in rows)} TIME={sum(r['time'] for r in rows)}")
print(f"WR (excl TIME): {len(wins_l)}/{len(excl)} = {len(wins_l)/len(excl)*100:.1f}%")

print("\n=== По режиму (excl TIME) ===")
for regime in ['TRENDING', 'DRIFT', 'RANGING']:
    b = [r for r in excl if r['regime'] == regime]
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        print(f"  {regime:<12} n={len(b):>3}  WR={wr:.0f}%")

print("\n=== Vol ratio (excl TIME) ===")
for lo, hi in [(0, 1.5), (1.5, 2.5), (2.5, 5), (5, 100)]:
    b = [r for r in excl if lo <= r['vol'] < hi]
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        print(f"  vol {lo}-{hi:<6}  n={len(b):>3}  WR={wr:.0f}%")

print("\n=== ADX_1H (excl TIME) ===")
for lo, hi in [(0, 20), (20, 30), (30, 40), (40, 100)]:
    b = [r for r in excl if lo <= r['adx'] < hi]
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        print(f"  ADX {lo}-{hi:<4}  n={len(b):>3}  WR={wr:.0f}%")

print("\n=== FVG confirmed (excl TIME) ===")
for fvg, label in [(True, 'yes'), (False, 'no ')]:
    b = [r for r in excl if r['fvg'] == fvg]
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        print(f"  fvg={label}  n={len(b):>3}  WR={wr:.0f}%")

print("\n=== TRENDING: Vol breakdown ===")
for lo, hi in [(0, 1.5), (1.5, 3), (3, 100)]:
    b = [r for r in excl if lo <= r['vol'] < hi and r['regime'] == 'TRENDING']
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        print(f"  TRENDING vol {lo}-{hi:<6}  n={len(b):>3}  WR={wr:.0f}%")

print("\n=== TP vs SL средние значения ===")
print(f"  {'':12}  vol_avg  adx_avg")
print(f"  {'TP':12}  {sum(r['vol'] for r in wins_l)/len(wins_l):.2f}     {sum(r['adx'] for r in wins_l)/len(wins_l):.1f}")
print(f"  {'SL':12}  {sum(r['vol'] for r in sls_l)/len(sls_l):.2f}     {sum(r['adx'] for r in sls_l)/len(sls_l):.1f}")

print("\n=== По дням (excl TIME) ===")
for day in sorted(set(r['ts'][:10] for r in rows)):
    b = [r for r in excl if r['ts'].startswith(day)]
    if b:
        wr = sum(r['win'] for r in b) / len(b) * 100
        mark = " <-- сегодня" if day == '2026-05-15' else ""
        print(f"  {day}  n={len(b):>3}  WR={wr:.0f}%{mark}")
