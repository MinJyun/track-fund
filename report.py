"""比對相鄰兩個資料日的持股，產出每日買賣報告（Markdown）。

這五檔主動式 ETF 皆為現金申購/買回：申贖進出的是現金，不會直接改變持股，
所以「股數差」就是經理人的實際買賣，不需要按規模校正。
（申購湧入後的布建買盤、買回後的變現賣壓，也是真實下單，會如實反映在股數差。）
流通單位數變化列在報告開頭，供判讀「這些買賣是調整持股還是消化申贖」。
"""
import sqlite3
from pathlib import Path

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


def diff_etf(conn: sqlite3.Connection, etf: str, prev_d: str, curr_d: str):
    curr, prev = _holdings(conn, etf, curr_d), _holdings(conn, etf, prev_d)
    fc, fp = _fund_day(conn, etf, curr_d), _fund_day(conn, etf, prev_d)

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
        delta = c["shares"] - p["shares"]
        if delta:
            est = (price(code) or 0) * delta
            item = (code, c["name"], p["shares"], c["shares"], delta, est,
                    c["weight"] - p["weight"])
            (inc if delta > 0 else dec).append(item)
    inc.sort(key=lambda x: -abs(x[5]))
    dec.sort(key=lambda x: -abs(x[5]))
    new.sort(key=lambda x: -(x[3] or 0))
    gone.sort(key=lambda x: -(x[3] or 0))

    du = ((fc["units"] - fp["units"]) / fp["units"] * 100
          if fc["units"] and fp["units"] else 0)
    L = [f"# {etf} 持股變化：{prev_d} → {curr_d}", ""]
    L.append(f"- 流通單位數：{fp['units']:,.0f} → {fc['units']:,.0f}"
             f"（{du:+.2f}%，現金申贖，不直接反映在持股）")
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
            L += [f"## {title}", "",
                  "| 股票 | 前日股數 | 今日股數 | 變化 | 估計金額 | 權重變化 |",
                  "|---|---:|---:|---:|---:|---:|"]
            L += [f"| {c} {n} | {ps:,.0f} | {cs:,.0f} | {d:+,.0f} "
                  f"| {money(est) if est else '—'} | {dw:+.2f}% |"
                  for c, n, ps, cs, d, est, dw in items] + [""]
    if not (new or gone or inc or dec):
        L += ["（持股無任何變化）", ""]
    return "\n".join(L)


def write_reports(conn: sqlite3.Connection, etfs) -> list:
    """為每檔 ETF 的所有相鄰資料日產出報告（既有檔案冪等重算覆蓋）。"""
    written = []
    for etf in etfs:
        dates = [r[0] for r in conn.execute(
            "SELECT data_date FROM fund_day WHERE etf=? ORDER BY data_date",
            (etf,))]
        if len(dates) < 2:
            print(f"[report] {etf}: 資料不足兩天，略過")
            continue
        for prev_d, curr_d in zip(dates, dates[1:]):
            text = diff_etf(conn, etf, prev_d, curr_d)
            out = REPORT_DIR / curr_d
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{etf}.md"
            path.write_text(text, encoding="utf-8")
            written.append(path)
        print(f"[report] {etf}: {dates[0]} ～ {dates[-1]} "
              f"共 {len(dates) - 1} 份報告")
    return written
