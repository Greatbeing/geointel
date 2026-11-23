#!/usr/bin/env python3
"""market_collect.py — 市场硬数据抓取（Week 2）。

零依赖，只用标准库。来源全部是公开、无 key 的：
  - FRED fredgraph.csv（美联储利率、美债收益率、汇率、美元指数）
  - ECB eurofxref-daily.xml（欧元参考汇率；FRED 汇率失败时的备用）
  - Frankfurter API（ECB 口径的公开汇率 JSON；备用）
抓到的数据写入 intel.db 的 market_series 表（见 store.py）。
不编造数据：某个源不通就跳过并报告，绝不拿旧值冒充新值。

用法：
  python3 market_collect.py [--days 35] [--prune-days 180]
  python3 market_collect.py --check        # 只测三个源通不通，不写库
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store  # noqa: E402

UA = {"User-Agent": "curl/7.81.0"}
FX_FRESH_BDAYS = 2
TIMEOUT = 30

# series: 中文名 / 单位 / 显示格式 / 来源
SERIES = {
    "DFF":      {"label": "美联邦基金利率", "unit": "%",    "fmt": "{:.2f}%",  "kind": "fred"},
    "DGS2":     {"label": "美债 2 年收益率", "unit": "%",    "fmt": "{:.2f}%",  "kind": "fred"},
    "DGS10":    {"label": "美债 10 年收益率", "unit": "%",   "fmt": "{:.2f}%",  "kind": "fred"},
    "US210":    {"label": "美债 2–10 年利差", "unit": "pp",  "fmt": "{:+.2f}pp", "kind": "derived"},
    "USDCNY":   {"label": "美元/人民币", "unit": "",         "fmt": "{:.2f}",   "kind": "fred"},
    "EURUSD":   {"label": "欧元/美元", "unit": "",           "fmt": "{:.4f}",   "kind": "fred"},
    "USDJPY":   {"label": "美元/日元", "unit": "",           "fmt": "{:.1f}",   "kind": "fred"},
    "DTWEXBGS": {"label": "美元广义指数", "unit": "",         "fmt": "{:.1f}",   "kind": "fred"},
}

# FRED 代码 -> 内部序列名
FRED_MAP = {
    "DFF": "DFF", "DGS2": "DGS2", "DGS10": "DGS10",
    "DEXCHUS": "USDCNY", "DEXUSEU": "EURUSD", "DEXJPUS": "USDJPY",
    "DTWEXBGS": "DTWEXBGS",
}

# 仪表条上展示的序列（按行分组）
DASHBOARD_ROWS = [
    ["DFF", "DGS2", "DGS10", "US210"],
    ["USDCNY", "EURUSD", "USDJPY", "DTWEXBGS"],
]


def _get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_fred_csv(text):
    """解析 fredgraph.csv。返回 [(date, value)]，跳过缺失值（'.'）。
    表头可能是 observation_date,<id> 或 DATE,VALUE。"""
    rows = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return rows
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in ("", "."):
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def parse_ecb_xml(text):
    """解析 eurofxref-daily.xml。返回 (date, {currency: rate})。"""
    root = ET.fromstring(text)
    date_s, rates = None, {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "Cube" and "time" in el.attrib:
            date_s = el.attrib["time"]
        elif tag == "Cube" and "currency" in el.attrib and "rate" in el.attrib:
            try:
                rates[el.attrib["currency"]] = float(el.attrib["rate"])
            except ValueError:
                pass
    return date_s, rates


def parse_frankfurter(text):
    """解析 Frankfurter JSON。返回 (date, {currency: rate})，base=USD。"""
    d = json.loads(text)
    return d.get("date"), {k: float(v) for k, v in (d.get("rates") or {}).items()}


def fetch_fred(series_id, start, end):
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
           f"&cosd={start}&coed={end}")
    return parse_fred_csv(_get(url).decode("utf-8", "replace"))


def fetch_ecb():
    text = _get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml").decode("utf-8", "replace")
    return parse_ecb_xml(text)


def fetch_frankfurter():
    text = _get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY,EUR,JPY").decode("utf-8", "replace")
    return parse_frankfurter(text)


def derive_spread(rows):
    """从 DGS2/DGS10 行派生 2–10 年利差行（同一日期相减）。"""
    by_date = {}
    for r in rows:
        if r["series"] in ("DGS2", "DGS10"):
            by_date.setdefault(r["date"], {})[r["series"]] = r["value"]
    out = []
    for d in sorted(by_date):
        pair = by_date[d]
        if "DGS2" in pair and "DGS10" in pair:
            out.append({"series": "US210", "date": d,
                        "value": round(pair["DGS10"] - pair["DGS2"], 4),
                        "source": "FRED-derived"})
    return out


def collect(days=35):
    """抓一轮。返回 (rows, failed)，rows 为待写入 market_series 的行。"""
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    rows, failed = [], []

    # 1. FRED：利率 + 汇率 + 美元指数
    for fred_id, name in FRED_MAP.items():
        try:
            got = fetch_fred(fred_id, start, end)
        except Exception as e:  # noqa: BLE001 — 单个源失败不挡住其他源
            failed.append(f"FRED/{fred_id}: {e}")
            continue
        if not got:
            failed.append(f"FRED/{fred_id}: 空数据")
            continue
        for d, v in got:
            rows.append({"series": name, "date": d, "value": v, "source": "FRED"})

    # 2. 2–10 年利差（派生）：同一日期 DGS10 − DGS2
    rows.extend(derive_spread(rows))

    # 3. 汇率备用：FRED 没给出的 FX 序列才用 ECB / Frankfurter 补
    fred_fx_newest = {}
    for r in rows:
        if r["series"] in ("USDCNY", "EURUSD", "USDJPY"):
            if r["date"] > fred_fx_newest.get(r["series"], ""):
                fred_fx_newest[r["series"]] = r["date"]
    # 只有足够新（≤ FX_FRESH_BDAYS 个工作日）的 FRED 汇率才算“已有”，否则仍去备用源取最新一天
    have_fx = {n for n, d in fred_fx_newest.items()
               if business_days_between(date.fromisoformat(d), today) <= FX_FRESH_BDAYS}
    if len(have_fx) < 3:
        fx = {}
        try:
            ecb_date, ecb = fetch_ecb()
            if ecb_date and "USD" in ecb and "CNY" in ecb:
                fx["USDCNY"] = (ecb_date, ecb["CNY"] / ecb["USD"], "ECB")
            if ecb_date and "USD" in ecb:
                fx["EURUSD"] = (ecb_date, 1.0 / ecb["USD"], "ECB")
            if ecb_date and "USD" in ecb and "JPY" in ecb:
                fx["USDJPY"] = (ecb_date, ecb["JPY"] / ecb["USD"], "ECB")
        except Exception as e:  # noqa: BLE001
            failed.append(f"ECB: {e}")
        if len(fx) < 3:
            try:
                f_date, f_rates = fetch_frankfurter()
                if f_date:
                    if "CNY" in f_rates:
                        fx.setdefault("USDCNY", (f_date, f_rates["CNY"], "Frankfurter"))
                    if "EUR" in f_rates:
                        fx.setdefault("EURUSD", (f_date, 1.0 / f_rates["EUR"], "Frankfurter"))
                    if "JPY" in f_rates:
                        fx.setdefault("USDJPY", (f_date, f_rates["JPY"], "Frankfurter"))
            except Exception as e:  # noqa: BLE001
                failed.append(f"Frankfurter: {e}")
        for name in ("USDCNY", "EURUSD", "USDJPY"):
            if name not in have_fx and name in fx:
                d, v, src = fx[name]
                if d <= fred_fx_newest.get(name, ""):
                    continue  # 备用源不比 FRED 新，不重复写
                rows.append({"series": name, "date": d, "value": round(v, 6), "source": src})
        missing = [n for n in ("USDCNY", "EURUSD", "USDJPY")
                   if n not in have_fx and n not in fx]
        if missing:
            failed.append(f"FX 备用源也未补上：{', '.join(missing)}")

    return rows, failed


def business_days_between(older, newer):
    """older 到 newer 之间经过的工作日数（不含周末；older 当天不计）。"""
    if newer <= older:
        return 0
    n, d = 0, older
    while d < newer:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _age(newest, today):
    return business_days_between(date.fromisoformat(newest), today)


def ensure_fresh(max_age_days=5):
    """保证市场数据新鲜：最新数据超过 max_age_days 天就抓一轮。
    供 digest.py 在出简报前调用（best-effort，失败不阻断简报）。
    返回 True 表示数据新鲜可用。"""
    latest = store.latest_market()
    today = date.today()
    fresh = False
    if latest:
        newest = max(v["date"] for v in latest.values())
        try:
            fresh = _age(newest, today) <= max_age_days
        except ValueError:
            fresh = False
    if not fresh:
        try:
            rows, _failed = collect()
            if rows:
                store.upsert_market(rows)
                latest = store.latest_market()
                newest = max(v["date"] for v in latest.values())
                return _age(newest, today) <= max_age_days
        except Exception:  # noqa: BLE001 — 抓取失败也不阻断简报
            return False
    return fresh


def dashboard_strip(max_age_days=5):
    """仪表条（markdown，供 digest.py 贴到简报顶部）。
    数据缺失或最新数据超过 max_age_days 个工作日时，返回一句“暂无更新”，不编数字。"""
    latest = store.latest_market()
    today = date.today()
    newest = None
    if latest:
        try:
            newest = max(v["date"] for v in latest.values())
            if _age(newest, today) > max_age_days:
                newest = None
        except ValueError:
            newest = None
    if not newest:
        return "## 核心利率 / 汇率\n市场数据暂无更新。\n"
    lines = [f"## 核心利率 / 汇率（截至 {newest}）"]
    for group in DASHBOARD_ROWS:
        parts = []
        for name in group:
            info = SERIES[name]
            row = latest.get(name)
            if row is None:
                continue
            text = f"{info['label']} {info['fmt'].format(row['value'])}"
            if row["date"] != newest:
                # 该序列比最新日期旧：把它的实际日期标出来，不冒充最新值
                text += f"（{row['date'][5:]}）"
            parts.append(text)
        if parts:
            lines.append("- " + " · ".join(parts))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓取市场硬数据写入 market_series")
    ap.add_argument("--days", type=int, default=35, help="回溯天数（默认 35）")
    ap.add_argument("--prune-days", type=int, default=180, help="删除早于此天数的旧行")
    ap.add_argument("--check", action="store_true", help="只测三个源通不通，不写库")
    args = ap.parse_args(argv)

    if args.check:
        ok = []
        _end = date.today().isoformat()
        _start = (date.today() - timedelta(days=7)).isoformat()
        for label, fn in (("FRED", lambda: fetch_fred("DFF", _start, _end)),
                          ("ECB", fetch_ecb),
                          ("Frankfurter", fetch_frankfurter)):
            try:
                fn()
                ok.append(f"{label} OK")
            except Exception as e:  # noqa: BLE001
                ok.append(f"{label} FAIL: {e}")
        print("\n".join(ok))
        return 0 if all("OK" in s for s in ok) else 1

    rows, failed = collect(days=args.days)
    n = store.upsert_market(rows) if rows else 0
    pruned = store.prune_market(days=args.prune_days)
    latest = store.latest_market()
    for name in ("DFF", "DGS2", "DGS10", "US210", "USDCNY", "EURUSD", "USDJPY", "DTWEXBGS"):
        row = latest.get(name)
        if row:
            print(f"{name}: {row['value']} ({row['date']}, {row['source']})")
    print(f"market_collect: {n} 行写入，{pruned} 行过期删除" + (f"；失败：{'; '.join(failed)}" if failed else ""))
    return 0 if rows else 2


if __name__ == "__main__":
    sys.exit(main())
