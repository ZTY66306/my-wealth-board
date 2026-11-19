#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动真实净值抓取与理财收益核算脚本
- 真实数据源：中国银行官网《代销理财产品净值》公开披露页面/检索接口
- 严格基于银行披露的真实单位净值计算：市值 = 份额 * 最新单位净值，日收益 = 份额 * (最新净值 - 上期净值)
- 绝不进行虚假线性估算；非交易日或暂未披露时如实保留上期真实净值
"""

import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# 强制北京时间 (UTC+8)
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
TODAY_STR = NOW.strftime("%Y-%m-%d")
TIME_STR = NOW.strftime("%Y-%m-%d %H:%M:%S")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LOG_LINES = []


def log(msg):
    line = f"[{datetime.now(CST).strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG_LINES.append(line)


def create_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_boc_agency_urls(session):
    """
    从中国银行金融市场频道动态发现“代销理财产品净值”的真实页面与检索接口
    """
    candidates = []
    entry_urls = [
        "https://www.bankofchina.com/fimarkets/",
        "https://www.boc.cn/fimarkets/",
    ]
    for entry in entry_urls:
        try:
            r = session.get(entry, timeout=15)
            r.encoding = "utf-8"
            log(f"GET {entry} -> {r.status_code} (len={len(r.text)})")
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(strip=True)
                href = a["href"].strip()
                if any(k in txt for k in ["代销理财", "理财产品净值", "资管产品净值", "净值"]):
                    full_url = urllib.parse.urljoin(entry, href)
                    log(f"  发现入口: [{txt}] -> {full_url}")
                    candidates.append((txt, full_url))
            if candidates:
                break
        except Exception as e:
            log(f"访问 {entry} 异常: {e}")
    return candidates


def parse_nav_from_html_table(html_text, target_code, target_name):
    """
    从中国银行净值 HTML 表格中解析目标产品的单位净值、累计净值、日增长率、截止日期
    """
    soup = BeautifulSoup(html_text, "html.parser")
    code_upper = (target_code or "").strip().upper()
    # 提取核心名称关键字（去掉括号等）
    short_name = re.sub(r"[（(].*?[）)]", "", target_name or "").strip()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
        for tr in rows[1:]:
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) < 4:
                continue
            row_text = " ".join(cols)
            row_upper = row_text.upper()

            matched = False
            if code_upper and len(code_upper) >= 5 and code_upper in row_upper:
                matched = True
            elif short_name and len(short_name) >= 6 and short_name in row_text:
                matched = True

            if matched:
                # 根据表头或列位置提取单位净值、日期
                unit_nav = None
                cum_nav = None
                nav_date = None
                daily_growth = None

                for idx, val in enumerate(cols):
                    h = headers[idx] if idx < len(headers) else ""
                    if "单位净值" in h or "份额净值" in h:
                        m = re.search(r"\d+\.\d+", val)
                        if m:
                            unit_nav = float(m.group(0))
                    elif "累计净值" in h:
                        m = re.search(r"\d+\.\d+", val)
                        if m:
                            cum_nav = float(m.group(0))
                    elif "日期" in h:
                        m = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", val)
                        if m:
                            nav_date = m.group(0).replace("/", "-").replace(".", "-")
                    elif "日净值增长率" in h or "日涨跌幅" in h:
                        m = re.search(r"-?\d+\.\d+", val)
                        if m:
                            daily_growth = float(m.group(0))

                # 兜底：若表头不标准，按正则提取首个 0.5~5.0 之间的 4 位小数及日期
                if unit_nav is None:
                    for val in cols[2:]:
                        m = re.fullmatch(r"\d+\.\d{3,6}", val.strip())
                        if m:
                            v = float(m.group(0))
                            if 0.5 <= v <= 10.0:
                                unit_nav = v
                                break
                if nav_date is None:
                    m = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", row_text)
                    if m:
                        nav_date = m.group(0).replace("/", "-").replace(".", "-")

                if unit_nav is not None:
                    return {
                        "unit_nav": unit_nav,
                        "cum_nav": cum_nav or unit_nav,
                        "nav_date": nav_date or TODAY_STR,
                        "daily_growth": daily_growth,
                        "raw_cols": cols,
                    }
    return None


def inspect_and_query_boc(session, holdings):
    """
    通过中国银行官网真实页面/检索接口批量拉取所有持仓的最新净值
    """
    results = {}
    candidates = discover_boc_agency_urls(session)

    # 优先选“代销理财”相关链接
    agency_urls = [u for txt, u in candidates if "代销" in txt]
    other_urls = [u for txt, u in candidates if "代销" not in txt and "净值" in txt]
    ordered_urls = agency_urls + other_urls

    # 扫描入口页，查找内嵌 iframe、form 或静态表格
    search_endpoints = []
    static_pages = []

    for url in ordered_urls[:6]:
        try:
            r = session.get(url, timeout=15)
            r.encoding = "utf-8"
            log(f"检查页面 {url} -> {r.status_code} (len={len(r.text)})")
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            # 检查是否有 form 检索接口（如 srh.bankofchina.com）
            for form in soup.find_all("form"):
                action = form.get("action") or url
                method = (form.get("method") or "GET").upper()
                full_action = urllib.parse.urljoin(url, action)
                inputs = {
                    inp.get("name"): inp.get("value", "")
                    for inp in form.find_all(["input", "select"])
                    if inp.get("name")
                }
                log(f"  发现表单: {method} {full_action} inputs={list(inputs.keys())}")
                search_endpoints.append((method, full_action, inputs, url))

            # 检查 iframe
            for iframe in soup.find_all("iframe", src=True):
                iframe_url = urllib.parse.urljoin(url, iframe["src"])
                log(f"  发现 iframe: {iframe_url}")
                static_pages.append(iframe_url)

            # 检查 script 中的 srh.bankofchina.com 或接口 URL
            for m in re.findall(r"https?://[a-zA-Z0-9._/-]*(?:srh|sourcedb|finas|dxlc)[a-zA-Z0-9._/?=&-]*", r.text):
                if m not in static_pages:
                    log(f"  脚本发现链接: {m}")
                    static_pages.append(m)

            # 直接尝试从当前页解析
            for h in holdings:
                if h["id"] not in results:
                    parsed = parse_nav_from_html_table(r.text, h.get("code"), h.get("name"))
                    if parsed:
                        log(f"  [命中] {h['name']} ({h.get('code')}): NAV={parsed['unit_nav']} 日期={parsed['nav_date']}")
                        results[h["id"]] = parsed
        except Exception as e:
            log(f"检查 {url} 异常: {e}")

    # 如果发现了检索表单，用每个产品的代码/名称去提交查询
    for method, action_url, base_inputs, referer in search_endpoints:
        if len(results) == len(holdings):
            break
        for h in holdings:
            if h["id"] in results:
                continue
            # 识别可能的代码输入框和名称输入框
            code_val = h.get("code", "")
            name_val = re.sub(r"[（(].*?[）)]", "", h.get("name", ""))
            payload = dict(base_inputs)
            for k in list(payload.keys()):
                kl = k.lower()
                if any(x in kl for x in ["code", "dm", "bm", "cpdm"]):
                    payload[k] = code_val
                elif any(x in kl for x in ["name", "mc", "cpmc", "key", "word", "search"]):
                    if not any(x in kl for x in ["code", "dm"]):
                        payload[k] = code_val if re.match(r"^[A-Za-z0-9]+$", code_val) else name_val[:8]
            try:
                hdrs = {"Referer": referer}
                if method == "POST":
                    resp = session.post(action_url, data=payload, headers=hdrs, timeout=15)
                else:
                    resp = session.get(action_url, params=payload, headers=hdrs, timeout=15)
                resp.encoding = "utf-8"
                parsed = parse_nav_from_html_table(resp.text, code_val, h.get("name"))
                if parsed:
                    log(f"  [表单命中] {h['name']} ({code_val}): NAV={parsed['unit_nav']} 日期={parsed['nav_date']}")
                    results[h["id"]] = parsed
                else:
                    log(f"  [表单未命中] {h['name']} ({code_val}) -> status={resp.status_code}, len={len(resp.text)}")
            except Exception as e:
                log(f"  表单查询 {code_val} 异常: {e}")

    return results


def send_notification(summary, products):
    serverchan_key = os.environ.get("SERVERCHAN_KEY")
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")
    if not serverchan_key and not pushplus_token:
        return

    title = f"理财真实收益播报：今日 {summary['daily_profit']:+.2f} 元"
    content_lines = [
        f"### 📊 真实净值核算汇总 ({TODAY_STR})",
        f"- **总持仓市值**: ¥{summary['total_assets']:,.2f}",
        f"- **最新单日收益**: ¥{summary['daily_profit']:+,.2f}",
        f"- **累计总收益**: ¥{summary['cumulative_profit']:+,.2f}",
        "",
        "### 🏦 产品净值明细",
    ]
    for p in products:
        tag = "✅实时" if p.get("sync_status") == "live" else "⏸️沿用"
        content_lines.append(
            f"- **{p['bank']} · {p['name']}** ({tag} 净值 {p['current_nav']}): "
            f"市值 ¥{p['market_value']:,.2f} | 日收益 {p['daily_profit']:+.2f} | 累计 {p['cumulative_profit']:+.2f}"
        )
    content = "\n".join(content_lines)

    if serverchan_key:
        try:
            requests.post(f"https://sctapi.ftqq.com/{serverchan_key}.send", data={"title": title, "desp": content}, timeout=10)
        except Exception:
            pass
    if pushplus_token:
        try:
            requests.post("http://www.pushplus.plus/send", json={"token": pushplus_token, "title": title, "content": content, "template": "markdown"}, timeout=10)
        except Exception:
            pass


def main():
    holdings_path = "holdings.json"
    data_path = "data.json"

    if not os.path.exists(holdings_path):
        log(f"[错误] 未找到 {holdings_path}")
        sys.exit(1)

    with open(holdings_path, "r", encoding="utf-8") as f:
        holdings = json.load(f)

    old_data = {"products": [], "history": []}
    if os.path.exists(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception:
            pass

    old_map = {p["id"]: p for p in old_data.get("products", [])}
    session = create_session()

    log("开始从中国银行官网拉取实时公开净值...")
    live_map = inspect_and_query_boc(session, holdings)
    log(f"拉取结束: 共 {len(holdings)} 只持仓，实时命中 {len(live_map)} 只")

    updated_products = []
    total_assets = 0.0
    total_daily = 0.0
    total_cum = 0.0

    for h in holdings:
        pid = h["id"]
        old_p = old_map.get(pid, {})
        shares = float(h.get("shares") or 0.0)
        principal = float(h.get("principal") or 0.0)
        live = live_map.get(pid)

        if live is not None:
            cur_nav = round(float(live["unit_nav"]), 4)
            nav_date = live.get("nav_date") or TODAY_STR
            old_nav = old_p.get("current_nav")
            old_date = old_p.get("nav_date")

            if old_nav is not None and nav_date != old_date:
                yest_nav = round(float(old_nav), 4)
                dp = round(shares * (cur_nav - yest_nav), 2)
            elif old_nav is not None and cur_nav != float(old_nav):
                yest_nav = round(float(old_nav), 4)
                dp = round(shares * (cur_nav - yest_nav), 2)
            else:
                # 同一净值日期重复跑，保留当期已算出的日收益，不重复叠加
                yest_nav = old_p.get("yesterday_nav", cur_nav)
                if live.get("daily_growth") is not None and old_p.get("daily_profit") is None:
                    dp = round(shares * cur_nav * (live["daily_growth"] / 100.0), 2)
                else:
                    dp = old_p.get("daily_profit", 0.0)

            mv = round(shares * cur_nav, 2)
            cp = round(mv - principal, 2)
            status = "live"
        else:
            # 未拉取到新净值时：绝不做虚假估算，如实沿用上期真实数据
            cur_nav = old_p.get("current_nav", 1.0)
            yest_nav = old_p.get("yesterday_nav", cur_nav)
            nav_date = old_p.get("nav_date", old_p.get("last_sync_date", TODAY_STR))
            mv = old_p.get("market_value", round(shares * cur_nav, 2))
            dp = old_p.get("daily_profit", 0.0)
            cp = round(mv - principal, 2)
            status = "unchanged"

        rec = {
            **h,
            "market_value": mv,
            "daily_profit": dp,
            "cumulative_profit": cp,
            "current_nav": cur_nav,
            "yesterday_nav": yest_nav,
            "nav_date": nav_date,
            "sync_status": status,
            "last_sync_date": TODAY_STR,
        }

        total_assets += mv
        total_daily += dp
        total_cum += cp
        updated_products.append(rec)

    history = [x for x in old_data.get("history", []) if x.get("date") != TODAY_STR]
    history.append({
        "date": TODAY_STR,
        "total_assets": round(total_assets, 2),
        "daily_profit": round(total_daily, 2),
        "cumulative_profit": round(total_cum, 2),
    })
    history = history[-90:]

    summary = {
        "total_assets": round(total_assets, 2),
        "daily_profit": round(total_daily, 2),
        "cumulative_profit": round(total_cum, 2),
    }

    result = {
        "updated_at": TIME_STR,
        "summary": summary,
        "products": updated_products,
        "history": history,
    }

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open("sync_debug.log", "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES) + "\n")

    log(f"[完成] 总资产={summary['total_assets']}, 今日收益={summary['daily_profit']}")
    send_notification(summary, updated_products)


if __name__ == "__main__":
    main()
