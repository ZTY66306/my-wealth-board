#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动真实净值抓取与理财收益核算脚本
- 真实数据源：中国银行官网《代销理财产品净值》(https://www.bankofchina.com/sourcedb/srfd6_2024/)
- 并发拉取官方公示表格，按产品代码或产品名称精确匹配最新单位净值与净值日期
- 严格按真实净值核算：市值 = 份额 * 最新单位净值，日收益 = 份额 * (最新净值 - 上期净值)
- 绝不做虚假线性估算；非交易日或暂未更新时如实保留上期真实净值
"""

import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

# 强制北京时间 (UTC+8)
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
TODAY_STR = NOW.strftime("%Y-%m-%d")
TIME_STR = NOW.strftime("%Y-%m-%d %H:%M:%S")

BOC_AGENCY_BASE = "https://www.bankofchina.com/sourcedb/srfd6_2024/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BOC_AGENCY_BASE,
}

LOG_LINES = []


def log(msg):
    line = f"[{datetime.now(CST).strftime('%H:%M:%S')}] {msg}"
    print(line)
    LOG_LINES.append(line)


def normalize_name(s):
    if not s:
        return ""
    s = re.sub(r"[（()）\s·•\-|｜]", "", s)
    return s.upper()


def parse_boc_page_rows(html_text):
    """
    解析中国银行 srfd6_2024 页面的净值表格，返回所有产品记录列表
    """
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    headers = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        first_cols = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if not any("净值" in c or "产品" in c for c in first_cols):
            continue
        headers = first_cols
        for tr in rows[1:]:
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) < 4:
                continue
            code = cols[0].strip()
            name = cols[1].strip()
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
                elif "日净值增长率" in h or "增长率" in h:
                    m = re.search(r"-?\d+\.\d+", val)
                    if m:
                        daily_growth = float(m.group(0))

            if unit_nav is None:
                for val in cols[2:]:
                    if re.fullmatch(r"\d+\.\d{3,6}", val.strip()):
                        v = float(val.strip())
                        if 0.5 <= v <= 10.0:
                            unit_nav = v
                            break
            if nav_date is None:
                m = re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", " ".join(cols))
                if m:
                    nav_date = m.group(0).replace("/", "-").replace(".", "-")

            if unit_nav is not None:
                items.append({
                    "code": code,
                    "name": name,
                    "unit_nav": unit_nav,
                    "cum_nav": cum_nav or unit_nav,
                    "nav_date": nav_date or TODAY_STR,
                    "daily_growth": daily_growth,
                })
    return headers, items


def detect_total_pages(html_text):
    """
    识别中国银行 TRS WCM 分页脚本中的总页数
    """
    # 常见格式: var m_nRecordCount = 3456; var m_nPageSize = 20; 或 createPageHTML(120, 0, "index", "html")
    m = re.search(r"createPageHTML\s*\(\s*(\d+)\s*,", html_text)
    if m:
        return int(m.group(1))
    m_rec = re.search(r"m_nRecordCount\s*=\s*(\d+)", html_text)
    m_size = re.search(r"m_nPageSize\s*=\s*(\d+)", html_text)
    if m_rec and m_size:
        rec = int(m_rec.group(1))
        size = max(1, int(m_size.group(1)))
        return (rec + size - 1) // size
    m_cnt = re.search(r"m_nPageCount\s*=\s*(\d+)", html_text)
    if m_cnt:
        return int(m_cnt.group(1))
    return 1


def match_holding(item, holding):
    h_code = (holding.get("code") or "").strip().upper()
    i_code = (item.get("code") or "").strip().upper()
    if h_code and i_code and re.match(r"^[A-Z0-9]+$", h_code):
        if h_code == i_code:
            return True

    i_norm = normalize_name(item.get("name", ""))
    if not i_norm:
        return False

    # 排除关键字（防止 30天持有期A 误匹配 30天持有期1号A）
    for ex in holding.get("exclude_keywords") or []:
        if normalize_name(ex) in i_norm:
            return False

    # 优先用显式配置的关键字列表（全部命中才算匹配）
    kws = holding.get("match_keywords")
    if kws:
        return all(normalize_name(k) in i_norm for k in kws)

    h_norm = normalize_name(holding.get("name", ""))
    if len(h_norm) >= 6 and h_norm in i_norm:
        return True
    return False


def fetch_all_boc_agency_navs(holdings, old_map=None):
    """
    温和限流 + 页码记忆优先 + 失败重试：抓取中国银行代销理财产品净值公示页
    """
    matched = {}
    old_map = old_map or {}
    session = requests.Session()
    session.headers.update(HEADERS)

    first_url = BOC_AGENCY_BASE + "index.html"
    try:
        r0 = session.get(first_url, timeout=15)
        r0.encoding = "utf-8"
        log(f"GET {first_url} -> {r0.status_code} (len={len(r0.text)})")
        if r0.status_code != 200:
            return matched
    except Exception as e:
        log(f"首页请求失败: {e}")
        return matched

    headers, first_items = parse_boc_page_rows(r0.text)
    total_pages = detect_total_pages(r0.text)
    log(f"表头: {headers}")
    log(f"第1页解析到 {len(first_items)} 条记录，识别总页数={total_pages}")

    for item in first_items:
        for h in holdings:
            if h["id"] not in matched and match_holding(item, h):
                item["found_page"] = 0
                matched[h["id"]] = item
                log(f"  [第1页命中] {h['name']} -> 代码={item['code']} 净值={item['unit_nav']} 日期={item['nav_date']}")

    if len(matched) == len(holdings) or total_pages <= 1:
        return matched

    max_pages = min(total_pages, 300)

    # 优先扫描上次命中的页码及其前后 ±2 页，大幅减少总请求数
    priority_pages = []
    seen = {0}
    for h in holdings:
        last_p = old_map.get(h["id"], {}).get("last_page")
        if isinstance(last_p, int) and 1 <= last_p < max_pages:
            for delta in (0, -1, 1, -2, 2):
                cand = last_p + delta
                if 1 <= cand < max_pages and cand not in seen:
                    seen.add(cand)
                    priority_pages.append(cand)

    remaining_pages = [p for p in range(1, max_pages) if p not in seen]
    scan_order = priority_pages + remaining_pages
    log(f"防限流温和模式(4线程+随机间隔+重试)：优先页={len(priority_pages)}页，总待扫={len(scan_order)}页")

    def fetch_page(page_idx):
        url = f"{BOC_AGENCY_BASE}index_{page_idx}.html"
        for attempt in range(1, 3):
            time.sleep(random.uniform(0.15, 0.35))
            try:
                resp = session.get(url, timeout=12)
                resp.encoding = "utf-8"
                if resp.status_code == 200:
                    _, items = parse_boc_page_rows(resp.text)
                    return page_idx, items
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * attempt)
                    continue
            except Exception:
                time.sleep(1.5 * attempt)
        return page_idx, []

    batch_size = 8
    fetched_count = 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i in range(0, len(scan_order), batch_size):
            if len(matched) == len(holdings):
                break
            batch = scan_order[i:i + batch_size]
            futures = [pool.submit(fetch_page, idx) for idx in batch]
            for fut in as_completed(futures):
                page_idx, items = fut.result()
                fetched_count += 1
                for item in items:
                    for h in holdings:
                        if h["id"] not in matched and match_holding(item, h):
                            item["found_page"] = page_idx
                            matched[h["id"]] = item
                            log(f"  [第{page_idx + 1}页命中] {h['name']} -> 代码={item['code']} 净值={item['unit_nav']} 日期={item['nav_date']}")
            if len(matched) == len(holdings):
                log(f"全部持仓已命中（本次仅请求 {fetched_count} 页），立即停止扫描！")
                break

    return matched


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
        tag = "✅实时" if p.get("sync_status") == "live" else "⏸️未变"
        content_lines.append(
            f"- **{p['bank']} · {p['name']}** ({tag} 净值 {p['current_nav']} / {p.get('nav_date', '')}): "
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

    log("开始抓取中国银行官网《代销理财产品净值》...")
    live_map = fetch_all_boc_agency_navs(holdings, old_map)
    log(f"拉取完成: 共 {len(holdings)} 只持仓，实时命中 {len(live_map)} 只")

    updated_products = []
    total_assets = 0.0
    total_daily = 0.0
    total_cum = 0.0

    USD_CNY = 7.20

    for h in holdings:
        pid = h["id"]
        old_p = old_map.get(pid, {})
        principal = float(h.get("principal") or 0.0)
        ref_mv = float(h.get("ref_mv") or principal)
        ref_daily = float(h.get("ref_daily") or 0.0)
        currency = h.get("currency", "CNY")
        fx = USD_CNY if currency == "USD" else 1.0
        live = live_map.get(pid)

        # 确定份额：优先使用 holdings 显式份额，其次沿用上次自动校准过的份额
        shares = float(h.get("shares") or 0.0)
        if shares <= 0 and old_p.get("shares", 0) > 0 and old_p.get("sync_status") == "live":
            shares = float(old_p["shares"])

        if live is not None:
            cur_nav = round(float(live["unit_nav"]), 4)
            nav_date = live.get("nav_date") or TODAY_STR
            real_code = live.get("code") or h.get("code")
            old_nav = old_p.get("current_nav")
            old_date = old_p.get("nav_date")
            old_status = old_p.get("sync_status")

            # 首次命中真实净值且尚未校准份额时：用 精确参考市值 ÷ 官网实时净值 反算份额
            if shares <= 0 and cur_nav > 0:
                shares = round(ref_mv / cur_nav, 2)
                log(f"  [自动校准份额] {h['name']}: ref_mv={ref_mv} / nav={cur_nav} -> shares={shares}")

            mv = round(shares * cur_nav, 2)
            cp = round(mv - principal, 2)

            if old_status == "live" and old_nav is not None and nav_date != old_date:
                # 新交易日真实净值变动：精确计算单日收益
                yest_nav = round(float(old_nav), 4)
                dp = round(shares * (cur_nav - yest_nav), 2)
            elif old_status == "live" and old_nav is not None and nav_date == old_date:
                # 同一净值日期，保留已算出的当期日收益，不重复加
                yest_nav = old_p.get("yesterday_nav", cur_nav)
                dp = old_p.get("daily_profit", ref_daily)
            else:
                # 首次接入真实净值：若官网有日增长率则换算，否则保留截图基准日收益
                yest_nav = cur_nav
                if live.get("daily_growth") is not None and ref_daily == 0.0:
                    dp = round(mv * (live["daily_growth"] / 100.0), 2)
                else:
                    dp = ref_daily

            status = "live"
        else:
            # 未命中时不造假，如实保留参考市值或上期记录
            cur_nav = old_p.get("current_nav", 1.0)
            yest_nav = old_p.get("yesterday_nav", cur_nav)
            nav_date = old_p.get("nav_date", TODAY_STR)
            real_code = h.get("code") or old_p.get("code", "")
            mv = old_p.get("market_value", ref_mv)
            if shares <= 0 and cur_nav > 0:
                shares = round(mv / cur_nav, 2)
            dp = old_p.get("daily_profit", ref_daily)
            cp = round(mv - principal, 2)
            status = "unchanged"

        rec = {
            **h,
            "code": real_code,
            "shares": shares,
            "currency": currency,
            "market_value": mv,
            "daily_profit": dp,
            "cumulative_profit": cp,
            "current_nav": cur_nav,
            "yesterday_nav": yest_nav,
            "nav_date": nav_date,
            "sync_status": status,
            "last_sync_date": TODAY_STR,
            "last_page": live.get("found_page") if live is not None else old_p.get("last_page"),
        }

        total_assets += mv * fx
        total_daily += dp * fx
        total_cum += cp * fx
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
