"""比對相鄰兩個資料日的持股，產出每日買賣報告（Markdown）。

核心校正：申購/買回造成基金規模變動時，全部持股會等比放大縮小，
不能直接把股數差當成經理人買賣。以流通單位數比值校正：

    主動調整股數 = 今日股數 - 昨日股數 × (今日流通單位數 / 昨日流通單位數)
"""
import sqlite3
from pathlib import Path

# 校正後變化量佔前日股數比例超過此值才列入加減碼（過濾規模校正的殘差雜訊）
MIN_CHANGE_PCT = 1.0

REPORT_DIR = Path(__file__).parent / "reports"


def _fund_day(conn, etf, dd):
    row = conn.execute(
        "SELECT nav, units, total_assets FROM fund_day "
        "WHERE etf=? AND data_date=?", (etf, dd)).fetchone()
    return {"nav": row[0], "units": row[1], "total_assets": row[2]}


def _holdings(conn, etf, dd):
    rows = conn.execute(
        "SELECT code, name, shares, amount, weight FROM holding "
        "WHERE etf=? AND data_date=?", (etf, dd)).fetchall()
    return {r[0]: {"name": r[1], "shares": r[2], "amount": r[3],
                   "weight": r[4]} for r in rows}


def diff_etf(conn: sqlite3.Connection, etf: str):
    """回傳 (markdown 文字, 比對用的兩個日期)；資料不足兩天回傳 None。"""
    dates = [r[0] for r in conn.execute(
        "SELECT data_date FROM fund_day WHERE etf=? "
        "ORDER BY data_date DESC LIMIT 2", (etf,))]
    if len(dates) < 2:
        return None
    curr_d, prev_d = dates
    curr, prev = _holdings(conn, etf, curr_d), _holdings(conn, etf, prev_d)
    fc, fp = _fund_day(conn, etf, curr_d), _fund_day(conn, etf, prev_d)
    ratio = fc["units"] / fp["units"] if fc["units"] and fp["units"] else 1.0

    def price(code):
        h = curr.get(code) or prev.get(code)
        return h["amount"] / h["shares"] if h["amount"] and h["shares"] else None

    new, gone, inc, dec = [], [], [], []
    for code in sorted(set(curr) | set(prev)):
        c, p = curr.get(code), prev.get(code)
        if p is None:
            new.append((code, c["name"], c["shares"], c["amount"]))
            continue
        if c is None:
            gone.append((code, p["name"], p["shares"],
                         (price(code) or 0) * p["shares"]))
            continue
        adj = c["shares"] - p["shares"] * ratio
        if p["shares"] and abs(adj) / p["shares"] * 100 >= MIN_CHANGE_PCT:
            est = (price(code) or 0) * adj
            item = (code, c["name"], p["shares"], c["shares"], adj, est,
                    c["weight"] - p["weight"])
            (inc if adj > 0 else dec).append(item)
    inc.sort(key=lambda x: -abs(x[5]))
    dec.sort(key=lambda x: -abs(x[5]))
    new.sort(key=lambda x: -(x[3] or 0))
    gone.sort(key=lambda x: -(x[3] or 0))

    L = [f"# {etf} 持股變化：{prev_d} → {curr_d}", ""]
    L.append(f"- 流通單位數：{fp['units']:,.0f} → {fc['units']:,.0f}"
             f"（規模校正比 {ratio:.4f}）")
    L.append(f"- 淨值：{fp['nav']} → {fc['nav']}")
    L.append(f"- 持股檔數：{len(prev)} → {len(curr)}")
    L.append("")

    def money(v):
        return f"{v/1e8:,.2f} 億" if abs(v) >= 1e8 else f"{v/1e4:,.0f} 萬"

    if new:
        L += ["## 新進場", "", "| 股票 | 股數 | 市值 |", "|---|---:|---:|"]
        L += [f"| {c} {n} | {s:,.0f} | {money(a) if a else '—'} |"
              for c, n, s, a in new] + [""]
    if gone:
        L += ["## 清倉", "", "| 股票 | 原股數 | 估計市值 |", "|---|---:|---:|"]
        L += [f"| {c} {n} | {s:,.0f} | {money(a) if a else '—'} |"
              for c, n, s, a in gone] + [""]
    for title, items in (("加碼", inc), ("減碼", dec)):
        if items:
            L += [f"## {title}（規模校正後 ≥ {MIN_CHANGE_PCT}%）", "",
                  "| 股票 | 前日股數 | 今日股數 | 校正後變化 | 估計金額 | 權重變化 |",
                  "|---|---:|---:|---:|---:|---:|"]
            L += [f"| {c} {n} | {ps:,.0f} | {cs:,.0f} | {adj:+,.0f} "
                  f"| {money(est) if est else '—'} | {dw:+.2f}% |"
                  for c, n, ps, cs, adj, est, dw in items] + [""]
    if not (new or gone or inc or dec):
        L += ["（無顯著變化）", ""]
    return "\n".join(L), (prev_d, curr_d)


def write_reports(conn: sqlite3.Connection, etfs) -> list:
    written = []
    for etf in etfs:
        result = diff_etf(conn, etf)
        if result is None:
            print(f"[report] {etf}: 資料不足兩天，略過")
            continue
        text, (prev_d, curr_d) = result
        out = REPORT_DIR / curr_d
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{etf}.md"
        path.write_text(text, encoding="utf-8")
        written.append(path)
        print(f"[report] {etf}: {prev_d} → {curr_d} 寫入 {path}")
    return written
