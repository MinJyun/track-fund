"""把最新持股異動摘要推到 LINE 群組（Messaging API push）。

設定檔 line_config.json（已 gitignore，勿入版控）：
    {"channel_access_token": "<長期權杖>", "to": "<群組ID C開頭 或 使用者ID U開頭>"}

未建設定檔時靜默跳過（排程照跑不報錯）。
同一組資料日只會發一次（狀態記在 data/notify_state.json），
所以 18:00 與 21:30 兩個排程時段不會重複推播。
"""
import json
import sys
from datetime import date
from pathlib import Path

import requests

import report
import store
from fetchers import FUNDS

CONFIG = Path(__file__).parent / "line_config.json"
STATE = store.BASE / "notify_state.json"
TOP_N = 5  # 每檔基金加碼/減碼各列前幾筆，其餘彙總為一行


def _money(v):
    return f"{v/1e8:,.1f}億" if abs(v) >= 1e8 else f"{v/1e4:,.0f}萬"


def _fund_summary(conn, etf):
    """回傳 (訊息行 list, 最新資料日)；資料不足兩天回傳 (None, 最新資料日)。"""
    dates = [r[0] for r in conn.execute(
        "SELECT data_date FROM fund_day WHERE etf=? "
        "ORDER BY data_date DESC LIMIT 2", (etf,))]
    if not dates:
        return None, None
    if len(dates) < 2:
        return None, dates[0]
    curr_d, prev_d = dates
    curr = report._holdings(conn, etf, curr_d)
    prev = report._holdings(conn, etf, prev_d)
    fc = report._fund_day(conn, etf, curr_d)
    fp = report._fund_day(conn, etf, prev_d)

    def price(code):
        h = curr.get(code) or prev.get(code)
        return h["amount"] / h["shares"] if h["amount"] and h["shares"] else 0

    new, gone, chg = [], [], []
    for code in set(curr) | set(prev):
        c, p = curr.get(code), prev.get(code)
        if p is None:
            new.append((c["amount"] or 0, f"🆕 {c['name']} {c['shares']:,.0f}股"
                        f"({_money(c['amount'])})" if c["amount"] else
                        f"🆕 {c['name']} {c['shares']:,.0f}股"))
        elif c is None:
            est = price(code) * p["shares"]
            gone.append((est, f"❌ 清倉 {p['name']}({_money(est)})"))
        elif c["shares"] != p["shares"]:
            d = c["shares"] - p["shares"]
            est = price(code) * d
            chg.append((abs(est), f"{'➕' if d > 0 else '➖'} {c['name']} "
                        f"{d:+,.0f}股({_money(est)})"))

    du = ((fc["units"] - fp["units"]) / fp["units"] * 100
          if fc["units"] and fp["units"] else 0)
    head = (f"▍{etf} {FUNDS[etf]['name']}（{prev_d[5:]}→{curr_d[5:]}，"
            f"申贖{du:+.1f}%）")
    if not (new or gone or chg):
        return [head, "持股無變動"], curr_d
    lines = [head]
    lines += [t for _, t in sorted(new, key=lambda x: -x[0])]
    lines += [t for _, t in sorted(gone, key=lambda x: -x[0])]
    chg.sort(key=lambda x: -x[0])
    lines += [t for _, t in chg[:TOP_N]]
    if len(chg) > TOP_N:
        lines.append(f"…另有 {len(chg) - TOP_N} 筆較小異動")
    return lines, curr_d


def build_message(conn):
    """回傳 (訊息文字, 各檔最新資料日 dict)。"""
    blocks, sig = [], {}
    for etf in FUNDS:
        lines, curr_d = _fund_summary(conn, etf)
        if curr_d:
            sig[etf] = curr_d
        if lines:
            blocks.append("\n".join(lines))
    text = f"📊 主動式ETF持股異動 {date.today():%m/%d}\n\n" + "\n\n".join(blocks)
    return text, sig


def push(token, to, text):
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}"},
        json={"to": to, "messages": [{"type": "text", "text": text[:4900]}]},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"LINE push 失敗 {r.status_code}: {r.text}")


def main():
    if not CONFIG.exists():
        print("[notify] 未設定 line_config.json，跳過推播")
        return
    cfg = json.loads(CONFIG.read_text())

    conn = store.connect()
    text, sig = build_message(conn)

    old = json.loads(STATE.read_text()) if STATE.exists() else {}
    if sig == old:
        print("[notify] 資料日未更新，跳過推播")
        return
    if "--dry-run" in sys.argv:
        print(text)
        return
    push(cfg["channel_access_token"], cfg["to"], text)
    STATE.write_text(json.dumps(sig, indent=1))
    print(f"[notify] 已推播（{len(text)} 字）")


if __name__ == "__main__":
    main()
