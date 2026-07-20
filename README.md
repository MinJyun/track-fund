# track-fund

追蹤五檔台灣主動式 ETF 的每日持股，比對相鄰兩個資料日推算經理人買賣。

| ETF | 名稱 | 資料來源 | 歷史回補 |
|---|---|---|---|
| 00981A | 主動統一台股增長 | 統一投信 GetPCF API (JSON) | 可 |
| 00403A | 主動統一升級50 | 統一投信 GetPCF API (JSON) | 可 |
| 00988A | 主動統一全球創新 | 統一投信 GetPCF API (JSON) | 可 |
| 00991A | 主動復華未來50 | 復華投信 assetsExcel API (xlsx) | 可 |
| 00990A | 主動元大AI新經濟 | 元大投信 bridge API (JSON) | 不可（只有最新一日，斷抓即缺） |

## 用法

```bash
python3 main.py daily                 # 抓最新 + 產報告（給每日排程用）
python3 main.py fetch                 # 只抓最新持股入庫
python3 main.py backfill --days 10    # 回補統一/復華歷史
python3 main.py report                # 只產報告
# --etf 00981A 可限定單檔，可重複
```

排程：`~/Library/LaunchAgents/com.minjyun.track-fund.plist` 於週一至週五 18:00
執行 `daily.sh`（抓取 + 產報告 + 自動 commit；統一 16:30 後才揭露當日資料）。
手動觸發：`launchctl kickstart gui/$(id -u)/com.minjyun.track-fund`。

## 資料存放

- `data/raw/{資料日}/{ETF}.json|xlsx` — 投信原始回應，供重新解析與備查
- `data/holdings.db` — SQLite：`fund_day`（淨值/流通單位數）、`holding`（個股股數/金額/權重）
- `reports/{資料日}/{ETF}.md` — 買賣報告：新進場、清倉、加碼、減碼

## 判讀注意

- 各家揭露延遲不同（統一當日、復華 T、元大約 T-2），diff 以「資料日」對齊，不是抓取日。
- 五檔皆為**現金申購/買回**：申贖進出的是現金，不會直接改變持股，
  所以股數差就是經理人實際買賣，報告不做規模校正。流通單位數變化列在
  報告開頭，供判讀買賣是「調整持股」還是「消化申贖」（大額申購後的
  布建買盤、買回後的變現賣壓也是真實下單）。
- 除權息、股票分割造成的股數跳動無法自動辨識，遇到異常大的變化請對照原始檔。
- 資料來源皆為未公開的官網內部 API，投信改版即失效；抓取失敗會印錯誤並以非零 exit code 結束。
