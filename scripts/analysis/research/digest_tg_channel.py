# -*- coding: utf-8 -*-
"""
digest_tg_channel.py — сжимает Telegram-канал в дайджест: вытаскивает посты с торговой
механикой / сетапами / мета-философией / данными-сигналами и отсеивает шум (gm/эмодзи/чарт-ссылки).
Чтобы читать 11 каналов выжимками, а не построчно. Read-only.
Usage: python digest_tg_channel.py "<path to ChatExport folder or result.json>"
"""
import sys, io, os, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SETUP = re.compile(r"сетап|setup|вход(?!ящ)|стоп|stop[- ]?loss|тейк|target|пробой|breakout|ренж|range|"
    r"накопл|аккумул|accumul|свип|sweep|ликвидн|liquidity|\bOTE\b|\bFVG\b|order ?block|\bOB\b|breaker|"
    r"kill ?zone|килл|сесси|session|дивергенц|divergence|imbalance|дисбаланс|MMSM|MMBM|MMXM|\bAMD\b|"
    r"реаккум|reaccum|equal high|equal low|\bEQH\b|\bEQL\b|манипуляц|expansion|экспанси", re.I)
META = re.compile(r"ожидани|\bwait|терпен|patience|селект|выборочн|less is more|80\s*%|дисциплин|"
    r"направлени[^.]{0,30}не[^.]{0,20}(зараб|деньг)|знать направлени|risk[- ]?reward|риск[- ]?менедж|"
    r"журнал сделок|overtrad|перетор|конвикш|conviction", re.I)
DATA = re.compile(r"on[- ]?chain|onchain|dominance|доминаци|BTC\.D|USDT\.D|ETHBTC|funding|фандинг|"
    r"open ?interest|открыт\w* интерес|\bOI\b|\bNFP\b|\bCPI\b|\bFOMC\b|анлок|unlock|листинг|listing|"
    r"\bTVL\b|domain|домен|whale|кит\b|кошельк|объ[её]м.{0,15}(зашкал|всплеск|аном)", re.I)
NOISE = re.compile(r"^(gm|gw|gn|/sound|/chart|\^+|lol|kek|rofl|\W{0,3})$", re.I)
TICK = re.compile(r"[#$]([A-Z]{2,7})\b")


def msg_text(m):
    t = m.get("text", "")
    if isinstance(t, str):
        return t
    return "".join(x if isinstance(x, str) else x.get("text", "") for x in t)


def main():
    p = sys.argv[1]
    if os.path.isdir(p):
        p = os.path.join(p, "result.json")
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    setup, meta, data = [], [], []
    ticks = {}
    n_txt = 0
    for m in d.get("messages", []):
        if m.get("type") != "message":
            continue
        txt = msg_text(m).strip()
        if not txt or NOISE.match(txt):
            continue
        n_txt += 1
        dt = m.get("date", "")[:10]
        for t in TICK.findall(txt):
            ticks[t] = ticks.get(t, 0) + 1
        short = re.sub(r"https?://\S+", "", txt).strip()
        short = short[:500] + ("…" if len(short) > 500 else "")
        if META.search(txt):
            meta.append((dt, short))
        elif SETUP.search(txt) and len(txt) > 60:   # сетап = содержательный, не однострочник
            setup.append((dt, short))
        elif DATA.search(txt):
            data.append((dt, short))

    print(f"КАНАЛ: {d.get('name')} | постов с текстом: {n_txt}")
    top = sorted(ticks.items(), key=lambda x: -x[1])[:20]
    print("ТОП тикеры:", ", ".join(f"{k}×{v}" for k, v in top), "\n")

    def dump(title, items, cap):
        print(f"\n{'='*70}\n{title}  (всего {len(items)}, показываю {min(cap,len(items))})\n{'='*70}")
        for dt, tx in items[:cap]:
            print(f"[{dt}] {tx}\n")

    dump("МЕТА / ФИЛОСОФИЯ (селективность, терпение, направление≠деньги)", meta, 25)
    dump("СЕТАПЫ / МЕХАНИКА (вход/стоп/ликвидность/сессии/структура)", setup, 70)
    dump("ДАННЫЕ-СИГНАЛЫ (on-chain / доминации / новости / OI / анлоки)", data, 40)


if __name__ == "__main__":
    main()
