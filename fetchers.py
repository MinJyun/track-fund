"""抓取三家投信的主動式 ETF 每日持股。

每個 fetcher 回傳統一格式的 snapshot dict：
{
    "etf": "00981A",
    "data_date": "2026-07-16",      # 投信揭露的資料日期（非抓取日期）
    "nav": 28.87,                    # 每單位淨值
    "units": 9367709000.0,           # 已發行受益權單位總數
    "total_assets": 270442966598.0,  # 基金淨資產(元)
    "holdings": [{"code", "name", "shares", "amount", "weight"}, ...],
    "raw": b"...",                   # 原始回應，供落檔備查
    "raw_ext": "json" | "xlsx",
}
"""
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from xml.etree import ElementTree as ET

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

FUNDS = {
    "00981A": {"issuer": "uni", "uni_code": "49YTW", "name": "主動統一台股增長"},
    "00403A": {"issuer": "uni", "uni_code": "63YTW", "name": "主動統一升級50"},
    "00988A": {"issuer": "uni", "uni_code": "61YTW", "name": "主動統一全球創新"},
    "00991A": {"issuer": "fuhhwa", "page_id": "ETF23", "name": "主動復華未來50"},
    "00990A": {"issuer": "yuanta", "name": "主動元大AI新經濟"},
}


# ---------------------------------------------------------------- 統一投信
_uni_session = None


def _uni_get_session():
    global _uni_session
    if _uni_session is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        # 先逛一次頁面拿 Nexusguard / ASP.NET session cookie
        r = s.get("https://www.ezmoney.com.tw/ETF/Transaction/PCF", timeout=30)
        r.raise_for_status()
        _uni_session = s
    return _uni_session


def _to_roc(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def _uni_date(v: str) -> str:
    """ezmoney 的 TranDate 有兩種格式：ISO 與 ASP.NET 的 /Date(毫秒)/。"""
    if v.startswith("/Date("):
        ms = int(re.search(r"-?\d+", v).group())
        tw = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ms / 1000, tz=tw).date().isoformat()
    return v[:10]


def fetch_uni(etf: str, target: date = None) -> dict:
    """target=None 抓最新一日；指定日期則查該日（specificDate=true）。"""
    s = _uni_get_session()
    payload = {
        "fundCode": FUNDS[etf]["uni_code"],
        "date": _to_roc(target or date.today()),
        "specificDate": target is not None,
    }
    r = s.post(
        "https://www.ezmoney.com.tw/ETF/Transaction/GetPCF",
        json=payload,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.ezmoney.com.tw/ETF/Transaction/PCF",
        },
        timeout=30,
    )
    r.raise_for_status()
    if "json" not in r.headers.get("Content-Type", ""):
        raise RuntimeError(f"ezmoney 回應不是 JSON（{etf}），網站可能改版或被擋")
    d = r.json()

    pcf = {row["PCFCode"]: row for row in d["pcf"]}
    if all(row["Amount"] == 0 for row in d["pcf"]):
        raise LookupError(f"ezmoney {etf} {payload['date']} 無資料")
    data_date = _uni_date(pcf["NAV"]["TranDate"])

    holdings = []
    for asset in d["asset"]:
        if asset["AssetCode"] != "ST" or not asset["Details"]:
            continue
        for row in asset["Details"]:
            holdings.append({
                "code": row["DetailCode"].strip(),
                "name": row["DetailName"].strip(),
                "shares": row["Share"],
                "amount": row["Amount"],
                "weight": row["NavRate"],
            })
    if not holdings:
        raise LookupError(f"ezmoney {etf} {data_date} 無持股明細")
    return {
        "etf": etf,
        "data_date": data_date,
        "nav": pcf["P_UNIT"]["Amount"],
        "units": pcf["OUT_UNIT"]["Amount"],
        "total_assets": pcf["NAV"]["Amount"],
        "holdings": holdings,
        "raw": r.content,
        "raw_ext": "json",
    }


# ---------------------------------------------------------------- 復華投信
def _xlsx_rows(content: bytes):
    z = zipfile.ZipFile(BytesIO(content))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    ss = [el.findtext("m:t", default="", namespaces=ns)
          for el in ET.parse(z.open("xl/sharedStrings.xml")).getroot()]
    sheet = ET.parse(z.open("xl/worksheets/sheet1.xml")).getroot()
    for r in sheet.findall(".//m:row", ns):
        vals = []
        for c in r.findall("m:c", ns):
            v = c.findtext("m:v", default="", namespaces=ns)
            if c.get("t") == "s" and v:
                v = ss[int(v)]
            vals.append(v)
        yield vals


def _num(s: str) -> float:
    return float(str(s).replace(",", "").replace("%", ""))


def fetch_fuhhwa(etf: str, target: date = None) -> dict:
    """target=None 從今天往回找最近一個有資料的日子（假日/未揭露會往前推）。"""
    page_id = FUNDS[etf]["page_id"]
    candidates = ([target] if target else
                  [date.today() - timedelta(days=i) for i in range(8)])
    content = None
    for d in candidates:
        url = (f"https://www.fhtrust.com.tw/api/assetsExcel/"
               f"{page_id}/{d:%Y%m%d}")
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200 and r.content[:2] == b"PK":
            content = r.content
            break
    if content is None:
        raise LookupError(f"fhtrust {etf} 找不到可用的持股 Excel")

    rows = list(_xlsx_rows(content))
    data_date = nav = units = total_assets = None
    holdings = []
    in_table = False
    for i, row in enumerate(rows):
        cell0 = row[0] if row else ""
        if cell0.startswith("日期"):
            m = re.search(r"(\d{4})/(\d{2})/(\d{2})", cell0)
            data_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        elif cell0 == "基金資產淨值":
            total_assets = _num(rows[i + 1][0])
        elif cell0 == "基金在外流通單位數":
            units = _num(rows[i + 1][0])
        elif cell0 == "基金每單位淨值":
            nav = _num(rows[i + 1][0])
        elif cell0 == "證券代號":
            in_table = True
        elif in_table and len(row) >= 5 and row[0]:
            holdings.append({
                "code": row[0].strip(),
                "name": row[1].strip(),
                "shares": _num(row[2]),
                "amount": _num(row[3]),
                "weight": _num(row[4]),
            })
    if not (data_date and holdings):
        raise RuntimeError(f"fhtrust {etf} Excel 格式解析失敗，格式可能改版")
    return {
        "etf": etf,
        "data_date": data_date,
        "nav": nav,
        "units": units,
        "total_assets": total_assets,
        "holdings": holdings,
        "raw": content,
        "raw_ext": "xlsx",
    }


# ---------------------------------------------------------------- 元大投信
def fetch_yuanta(etf: str, target: date = None) -> dict:
    """元大 API 只提供最新一日，target 僅接受 None。"""
    if target is not None:
        raise ValueError("元大 API 不支援查歷史日期")
    r = requests.get(
        "https://etfapi.yuantaetfs.com/ectranslation/api/bridge",
        params={
            "APIType": "ETFAPI", "FuncId": "PCF/Daily", "ticker": etf,
            "CompanyName": "YUANTAFUNDS", "AppName": "ETF",
            "Device": "3", "Platform": "ETF", "DeviceId": "null",
            "PageName": f"/product/detail/{etf}/ratio",
        },
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Referer": "https://www.yuantaetfs.com/",
                 "Origin": "https://www.yuantaetfs.com"},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    pcf = d["PCF"]
    td = pcf["trandate"]
    total_assets = pcf["totalav"]
    holdings = []
    for row in d["FundWeights"]["StockWeights"]:
        holdings.append({
            "code": row["code"].strip(),
            "name": row["name"].strip(),
            "shares": row["qty"],
            # 元大不給個股市值，用 淨資產×權重 估算（供報告排序用）
            "amount": round(total_assets * row["weights"] / 100),
            "weight": row["weights"],
        })
    return {
        "etf": etf,
        "data_date": f"{td[:4]}-{td[4:6]}-{td[6:8]}",
        "nav": pcf["nav"],
        "units": pcf["osunit"],
        "total_assets": total_assets,
        "holdings": holdings,
        "raw": r.content,
        "raw_ext": "json",
    }


FETCHERS = {"uni": fetch_uni, "fuhhwa": fetch_fuhhwa, "yuanta": fetch_yuanta}


def fetch(etf: str, target: date = None) -> dict:
    return FETCHERS[FUNDS[etf]["issuer"]](etf, target)
