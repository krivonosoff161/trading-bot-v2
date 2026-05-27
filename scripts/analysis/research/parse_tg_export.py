# -*- coding: utf-8 -*-
"""
parse_tg_export.py — извлекает чистый текст из Telegram Desktop JSON-экспорта канала.
Read-only. Вывод: метаданные канала + чистый текст постов (дата + текст) в .txt для разбора.
Usage: python parse_tg_export.py "<path to ChatExport folder or result.json>"
"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def msg_text(m):
    """Telegram text может быть строкой или списком (строки + entity-объекты)."""
    t = m.get("text", "")
    if isinstance(t, str):
        return t
    parts = []
    for x in t:
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            parts.append(x.get("text", ""))
    return "".join(parts)


def main():
    p = sys.argv[1]
    if os.path.isdir(p):
        p = os.path.join(p, "result.json")
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    msgs = d.get("messages", [])
    texts = []
    dates = []
    for m in msgs:
        if m.get("type") != "message":
            continue
        txt = msg_text(m).strip()
        dt = m.get("date", "")[:10]
        if txt:
            texts.append((dt, txt))
            dates.append(dt)
    print(f"КАНАЛ: {d.get('name')}  | type={d.get('type')} | id={d.get('id')}")
    print(f"всего сообщений: {len(msgs)} | с текстом: {len(texts)}")
    if dates:
        print(f"период: {min(dates)} .. {max(dates)}")
    # длина текстового корпуса
    total_chars = sum(len(t) for _, t in texts)
    print(f"корпус текста: {total_chars:,} символов (~{total_chars//4000} стр.)")
    # выгрузить в .txt рядом
    out = os.path.join(os.path.dirname(p), "_clean_text.txt")
    with open(out, "w", encoding="utf-8") as fh:
        for dt, txt in texts:
            fh.write(f"=== {dt} ===\n{txt}\n\n")
    print(f"чистый текст → {out}")


if __name__ == "__main__":
    main()
