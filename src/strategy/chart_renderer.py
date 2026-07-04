"""Chart rendering helpers extracted from scripts.analyze_chart."""

from __future__ import annotations

import textwrap
from datetime import datetime

import numpy as np

from src.strategy.indicators import calc_ema

_MSK_OFFSET = 3  # UTC+3, no DST


def _fmt_price(symbol: str, price) -> str:
    if price is None:
        return "—"
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{float(price):.1f}"
    if base in ("ETH", "SOL"):
        return f"{float(price):.2f}"
    return f"{float(price):.4f}"


def _to_msk(dt) -> "datetime":
    """Convert UTC datetime to Moscow time (UTC+3)."""
    from datetime import timedelta

    return dt + timedelta(hours=_MSK_OFFSET)


def render_annotated_png(image_path: str, summary_text: str, out_path: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("WARNING: Pillow not installed, skipping annotated PNG")
        return

    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size

    panel_w = 400
    padding = 14
    font_size = 14

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    max_chars = (panel_w - padding * 2) // 8
    wrapped = []
    for line in summary_text.split("\n"):
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width=max_chars) or [""])

    line_h = font_size + 4
    panel_h = max(ih, len(wrapped) * line_h + padding * 2)

    canvas = Image.new("RGB", (iw + panel_w, panel_h), (15, 15, 20))
    canvas.paste(img, (0, (panel_h - ih) // 2))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(iw, 0), (iw + panel_w, panel_h)], fill=(18, 20, 28))
    draw.line([(iw, 0), (iw, panel_h)], fill=(60, 80, 120), width=2)

    section_headers = {
        "📊 СЕЙЧАС НА РЫНКЕ",
        "✅ СТАВИМ ЛИМИТКУ",
        "⏸️ ЖДЁМ ПОДТВЕРЖДЕНИЯ",
        "📋 ПЛАН",
        "🚫 НЕТ СДЕЛКИ",
        "👃 ЗА ЧЕМ СЛЕДИМ",
        "❌ НЕ ДЕЛАТЬ",
        "⚠️ ПРАВИЛА ВХОДА",
        "🔄 ПОВТОРНЫЙ АНАЛИЗ",
    }

    def line_color(text: str) -> tuple:
        s = text.strip()
        if "═" in text:
            return (100, 160, 255)
        if s.startswith("Статус:"):
            if "ВХОД" in s:
                return (80, 210, 120)
            if "НАБЛЮДАЕМ" in s:
                return (220, 180, 60)
            return (150, 150, 155)
        if s.startswith("Направление:"):
            if "LONG" in s:
                return (80, 210, 120)
            if "SHORT" in s:
                return (210, 80, 80)
            return (150, 150, 155)
        if s.startswith("Тип:"):
            return (130, 180, 255)
        if any(s.startswith(h) for h in section_headers):
            return (80, 140, 210)
        if s.startswith("🛑"):
            return (220, 130, 80)
        if s.startswith("🎯"):
            return (255, 200, 80)
        if s.startswith("📈") or s.startswith("📉"):
            return (255, 200, 80)
        if s.startswith("("):
            return (145, 150, 165)
        return (190, 195, 210)

    y = padding
    for line in wrapped:
        draw.text((iw + padding, y), line, font=font, fill=line_color(line))
        y += line_h
        if y > panel_h - padding:
            break

    canvas.save(out_path, quality=92)
    print(f"Saved: {out_path}")


def render_chart(
    symbol: str,
    raw_15m: list,
    indicators: dict,
    captured_at: str,
    output_path: str,
    levels: dict | None = None,
    entry_signal: str | None = None,
    direction: str | None = None,
    trade_style: str | None = None,
    tf_label: str = "15m",
) -> None:
    try:
        import io
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
        from PIL import Image
    except ImportError as e:
        print(f"WARNING: Missing library for chart: {e}")
        return

    candles = list(reversed(raw_15m))
    n_show = min(60, len(candles))
    candles = candles[-n_show:]

    ts_list = [int(c[0]) for c in candles]
    opens = [float(c[1]) for c in candles]
    highs_c = [float(c[2]) for c in candles]
    lows_c = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    full = list(reversed(raw_15m))
    closes_full = np.array([float(c[4]) for c in full])
    highs_full = np.array([float(c[2]) for c in full])
    lows_full = np.array([float(c[3]) for c in full])
    ema20_slice = calc_ema(closes_full, 20)[-n_show:]
    ema50_slice = calc_ema(closes_full, 50)[-n_show:]

    bb_upper_arr = np.zeros(len(closes_full))
    bb_lower_arr = np.zeros(len(closes_full))
    for i in range(20, len(closes_full)):
        mid = np.mean(closes_full[i - 20 : i])
        std = np.std(closes_full[i - 20 : i], ddof=0)
        bb_upper_arr[i] = mid + 2 * std
        bb_lower_arr[i] = mid - 2 * std
    bb_upper_slice = bb_upper_arr[-n_show:]
    bb_lower_slice = bb_lower_arr[-n_show:]

    st_vals = np.zeros(len(closes_full))
    st_dirs = np.zeros(len(closes_full))
    atr_st = np.zeros(len(closes_full))
    for i in range(1, len(closes_full)):
        tr = max(
            highs_full[i] - lows_full[i],
            abs(highs_full[i] - closes_full[i - 1]),
            abs(lows_full[i] - closes_full[i - 1]),
        )
        atr_st[i] = (atr_st[i - 1] * 13 + tr) / 14 if i >= 14 else tr
    upper_st = np.zeros(len(closes_full))
    lower_st = np.zeros(len(closes_full))
    for i in range(14, len(closes_full)):
        mid = (highs_full[i] + lows_full[i]) / 2
        bu = mid + 3 * atr_st[i]
        bl = mid - 3 * atr_st[i]
        upper_st[i] = bu if bu < upper_st[i - 1] or closes_full[i - 1] > upper_st[i - 1] else upper_st[i - 1]
        lower_st[i] = bl if bl > lower_st[i - 1] or closes_full[i - 1] < lower_st[i - 1] else lower_st[i - 1]
        if st_vals[i - 1] == upper_st[i - 1]:
            st_vals[i] = lower_st[i] if closes_full[i] > upper_st[i] else upper_st[i]
        else:
            st_vals[i] = upper_st[i] if closes_full[i] < lower_st[i] else lower_st[i]
        st_dirs[i] = 1 if closes_full[i] > st_vals[i] else -1
    st_vals_slice = st_vals[-n_show:]
    st_dirs_slice = st_dirs[-n_show:]

    bg = "#0b0d14"
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    fig, (ax, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(10, 6.2),
        facecolor=bg,
        gridspec_kw={"height_ratios": [3.5, 1], "hspace": 0.06},
        sharex=True,
    )
    ax.set_facecolor(bg)
    ax_vol.set_facecolor(bg)

    for i, (o, h, low, c) in enumerate(zip(opens, highs_c, lows_c, closes)):
        col = "#26a69a" if c >= o else "#ef5350"
        ax.plot([i, i], [low, h], color=col, linewidth=0.7, zorder=2)
        body_h = max(abs(c - o), (h - low) * 0.015)
        ax.add_patch(mpatches.Rectangle((i - 0.35, min(c, o)), 0.7, body_h, facecolor=col, edgecolor=col, linewidth=0))

    for arr, col, label in [(ema20_slice, "#2196F3", "EMA20"), (ema50_slice, "#FF9800", "EMA50")]:
        pts = [(i, v) for i, v in enumerate(arr) if v > 0]
        if not pts:
            continue
        xi, yi = zip(*pts)
        ax.plot(xi, yi, color=col, linewidth=1.2, zorder=4)
        last_x, last_y = pts[-1]
        ax.text(
            last_x + 0.8,
            last_y,
            f"{label}: {_fmt_price(symbol, last_y)}",
            color=col,
            fontsize=6.5,
            va="center",
            ha="left",
            zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", facecolor=bg, edgecolor="none", alpha=0.85),
        )

    bb_u_pts = [(i, v) for i, v in enumerate(bb_upper_slice) if v > 0]
    bb_l_pts = [(i, v) for i, v in enumerate(bb_lower_slice) if v > 0]
    if bb_u_pts and bb_l_pts:
        ax.plot([p[0] for p in bb_u_pts], [p[1] for p in bb_u_pts], color="#7E57C2", linewidth=0.7, linestyle="--", alpha=0.6)
        ax.plot([p[0] for p in bb_l_pts], [p[1] for p in bb_l_pts], color="#7E57C2", linewidth=0.7, linestyle="--", alpha=0.6)

    for i in range(1, n_show):
        if st_vals_slice[i] == 0:
            continue
        col_st = "#26a69a" if st_dirs_slice[i] == 1 else "#ef5350"
        prev = st_vals_slice[i - 1] if st_vals_slice[i - 1] > 0 else st_vals_slice[i]
        ax.plot([i - 1, i], [prev, st_vals_slice[i]], color=col_st, linewidth=1.5, alpha=0.8, zorder=4)

    src = levels or {}
    for key, label, col, ls in [("sl", "SL", "#F44336", "--"), ("tp1", "TP1", "#FFD700", "--"), ("tp2", "TP2", "#FFA500", ":")]:
        if not src.get(key):
            continue
        price = float(src[key])
        ax.axhline(y=price, color=col, linestyle=ls, linewidth=0.9, alpha=0.85, zorder=5)
        ax.text(n_show - 1, price, f"  {label}: {_fmt_price(symbol, price)}", color=col, fontsize=7, va="bottom", ha="right")

    entry_price = src.get("entry_price")
    if entry_price and direction and entry_signal in ("ENTRY", "WAIT"):
        dir_text = "ВХОД LONG ▲" if direction == "buy" else "ВХОД SHORT ▼"
        ecol = "#00E676" if direction == "buy" else "#FF5252"
        ax.axhline(y=entry_price, color=ecol, linestyle="-", linewidth=1.2, alpha=0.9, zorder=6)
        ax.text(
            n_show // 2,
            entry_price,
            f"  {dir_text}: {_fmt_price(symbol, entry_price)}",
            color=ecol,
            fontsize=8,
            fontweight="bold",
            va="bottom",
            ha="center",
            zorder=8,
            bbox=dict(boxstyle="round,pad=0.2", facecolor=bg, edgecolor=ecol, alpha=0.9),
        )

    cur = float(indicators.get("15m", {}).get("close") or closes[-1])
    swing_highs = indicators.get("15m", {}).get("swing_highs") or []
    swing_lows = indicators.get("15m", {}).get("swing_lows") or []
    if swing_highs:
        sh = float(swing_highs[-1])
        if abs(sh - cur) / cur <= 0.08:
            ax.axhline(y=sh, color="#B39DDB", linestyle=":", linewidth=0.8, alpha=0.75)
    if swing_lows:
        sl_sw = float(swing_lows[-1])
        if abs(sl_sw - cur) / cur <= 0.08:
            ax.axhline(y=sl_sw, color="#80CBC4", linestyle=":", linewidth=0.8, alpha=0.75)

    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    ax_vol.bar(range(n_show), volumes, color=vol_colors, alpha=0.8, width=0.7)
    if len(volumes) >= 20:
        vol_sma = np.convolve(volumes, np.ones(20) / 20, mode="valid")
        ax_vol.plot(range(19, n_show), vol_sma, color="#888", linewidth=0.9)

    all_prices = lows_c + highs_c
    y_min = float(np.percentile(all_prices, 2))
    y_max = float(np.percentile(all_prices, 98))
    margin = (y_max - y_min) * 0.06
    ax.set_xlim(-0.8, n_show + 9)
    ax.set_ylim(y_min - margin, y_max + margin)
    ax.yaxis.tick_right()
    ax_vol.yaxis.tick_right()
    ax.tick_params(colors="#666", labelsize=7)
    ax_vol.tick_params(colors="#555", labelsize=6, left=False, right=True, labelleft=False, labelright=True)

    step = max(1, n_show // 8)
    ticks = list(range(0, n_show, step))
    ax_vol.set_xticks(ticks)
    ax_vol.set_xticklabels(
        [datetime.fromtimestamp(ts_list[i] / 1000).strftime("%H:%M") for i in ticks],
        color="#555",
        fontsize=6,
    )

    try:
        dt_utc = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        title = f"{symbol} · {tf_label} · {_to_msk(dt_utc).strftime('%Y-%m-%d %H:%M')} МСК"
    except Exception:
        title = f"{symbol} · {tf_label} · {captured_at[:16].replace('T', ' ')} UTC"
    ax.set_title(title, color="#7788aa", fontsize=8, loc="left", pad=5)

    if entry_signal and entry_signal != "NO_TRADE" and direction:
        dir_label = "▲ LONG" if direction == "buy" else "▼ SHORT"
        style_label = f" {trade_style}" if trade_style and trade_style != "NO_TRADE" else ""
        badge_col = "#00E676" if entry_signal == "ENTRY" else "#FFD700"
        ax.text(
            0.99,
            0.97,
            f"{dir_label}{style_label}  [{entry_signal}]",
            transform=ax.transAxes,
            color=badge_col,
            fontsize=9,
            fontweight="bold",
            va="top",
            ha="right",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=bg, edgecolor=badge_col, alpha=0.9),
        )

    plt.tight_layout(pad=0.4)
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    Image.open(buf).convert("RGB").save(output_path, quality=92)
    print(f"Saved: {output_path}")


def generate_annotated_png(image_path: str, summary_text: str, out_path: str) -> None:
    render_annotated_png(image_path, summary_text, out_path)


def generate_chart_png(
    raw_15m: list,
    result: dict,
    symbol: str,
    captured_at: str,
    out_path: str,
    llm_levels: dict | None = None,
    entry_signal: str = None,
    direction: str = None,
    trade_style: str = None,
    tf_label: str = "15m",
) -> None:
    render_chart(
        symbol=symbol,
        raw_15m=raw_15m,
        indicators=result,
        captured_at=captured_at,
        output_path=out_path,
        levels=llm_levels,
        entry_signal=entry_signal,
        direction=direction,
        trade_style=trade_style,
        tf_label=tf_label,
    )
