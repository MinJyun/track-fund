"""主動式 ETF 每日持股追蹤。

用法：
    python3 main.py fetch                 # 抓五檔最新持股並入庫
    python3 main.py backfill --days 10   # 回補統一/復華歷史（元大不支援）
    python3 main.py report                # 產出每檔最近兩日的買賣報告
    python3 main.py daily                 # fetch + report（給排程用）
"""
import argparse
import sys
import time
from datetime import date, timedelta

import fetchers
import report
import store


def cmd_fetch(conn, etfs):
    failures = []
    for etf in etfs:
        try:
            snap = fetchers.fetch(etf)
            store.save_snapshot(conn, snap)
            print(f"[fetch] {etf}: 資料日 {snap['data_date']}，"
                  f"{len(snap['holdings'])} 檔持股")
        except Exception as e:
            failures.append(etf)
            print(f"[fetch] {etf}: 失敗 — {e}", file=sys.stderr)
        time.sleep(1)
    return failures


def cmd_backfill(conn, etfs, days):
    for etf in etfs:
        if fetchers.FUNDS[etf]["issuer"] == "yuanta":
            print(f"[backfill] {etf}: 元大不提供歷史，略過")
            continue
        for i in range(1, days + 1):
            d = date.today() - timedelta(days=i)
            if d.weekday() >= 5:  # 週末必無資料
                continue
            try:
                # 統一的 date 參數是公告日，回應的資料日通常是前一交易日；
                # 一律以回應內的實際資料日入庫（重複日期會冪等覆蓋）。
                snap = fetchers.fetch(etf, d)
                store.save_snapshot(conn, snap)
                print(f"[backfill] {etf} 查 {d} → 資料日 {snap['data_date']}，"
                      f"{len(snap['holdings'])} 檔持股")
            except LookupError:
                print(f"[backfill] {etf} {d}: 無資料（休市或未揭露）")
            except Exception as e:
                print(f"[backfill] {etf} {d}: 失敗 — {e}", file=sys.stderr)
            time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["fetch", "backfill", "report", "daily"])
    ap.add_argument("--etf", action="append", choices=list(fetchers.FUNDS),
                    help="只處理指定 ETF（可重複），預設全部")
    ap.add_argument("--days", type=int, default=10, help="backfill 回補天數")
    args = ap.parse_args()
    etfs = args.etf or list(fetchers.FUNDS)

    conn = store.connect()
    failures = []
    if args.command in ("fetch", "daily"):
        failures = cmd_fetch(conn, etfs)
    if args.command == "backfill":
        cmd_backfill(conn, etfs, args.days)
    if args.command in ("report", "daily"):
        report.write_reports(conn, etfs)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
