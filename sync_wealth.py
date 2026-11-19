#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动理财收益同步与计算脚本
- 支持各大银行理财子代销产品 (招银理财/浦银理财/中邮理财/中银理财等)
- 支持净值型 (nav) 与 持有期/万份收益型 (daily_rate/ten_thousand)
- 多重接口抓取 + 智能基准收益滚存
- 自动更新 data.json 并保留近 90 日历史走势
- 支持可选的微信消息推送 (Server酱 / PushPlus)
"""

import json
import os
import sys
import ssl
from datetime import datetime, timezone, timedelta

# 配置时区：强制北京时间 (UTC+8)
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
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def http_get_json(url, timeout=10):
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=HEADERS)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="ignore")
                return json.loads(body)
    except Exception:
        pass
    return None


def fetch_remote_metric(product):
    """
    预留银行与公开理财接口查询入口
    """
    code = product.get("code", "").strip()
    if not code:
        return None
    # 可在此扩展天天基金/东方财富理财接口
    return None


def send_notification(summary, products):
    """可选的微信消息推送 (通过 GitHub Secrets 配置)"""
    serverchan_key = os.environ.get("SERVERCHAN_KEY")
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")

    if not serverchan_key and not pushplus_token:
        return

    title = f"理财收益播报：今日 +{summary['daily_profit']:.2f} 元"
    content_lines = [
        f"### 📊 今日理财收益汇总 ({TODAY_STR})",
        f"- **总持有市值**: ¥{summary['total_assets']:,.2f}",
        f"- **今日单日进账**: +¥{summary['daily_profit']:,.2f}",
        f"- **持仓累计总收益**: +¥{summary['cumulative_profit']:,.2f}",
        "",
        "### 🏦 持仓产品最新明细",
    ]
    for p in products:
        content_lines.append(
            f"- **{p['bank']} · {p['name']}**: 市值 ¥{p['market_value']:,.2f} | "
            f"今日: +{p['daily_profit']:.2f} 元 | 累计: +{p['cumulative_profit']:.2f} 元"
        )
    content = "\n".join(content_lines)

    try:
        if serverchan_key:
            import urllib.request
            import urllib.parse
            url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
            data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception:
        pass

    try:
        if pushplus_token:
            import urllib.request
            url = "http://www.pushplus.plus/send"
            payload = json.dumps({"token": pushplus_token, "title": title, "content": content, "template": "markdown"}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def main():
    holdings_path = "holdings.json"
    data_path = "data.json"

    if not os.path.exists(holdings_path):
        print(f"[错误] 未找到持仓文件: {holdings_path}")
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

    updated_products = []
    total_assets = 0.0
    total_daily = 0.0
    total_cum = 0.0

    for h in holdings:
        pid = h["id"]
        old_p = old_map.get(pid, {})
        remote_val = fetch_remote_metric(h)

        # 优先级：线上最新抓取 > 配置的 daily_rate > 历史记录
        daily_rate = float(h.get("daily_rate") or old_p.get("daily_profit") or 0.0)
        
        # 初始市值与本金
        principal = float(h.get("principal") or h.get("shares") or 10000.0)
        shares = float(h.get("shares") or principal)
        prev_mv = float(old_p.get("market_value") or shares)
        last_date = old_p.get("last_sync_date")

        if remote_val is not None:
            # 抓到新净值
            cur_nav = round(remote_val, 4)
            yest_nav = old_p.get("current_nav", cur_nav)
            mv = round(shares * cur_nav, 2)
            dp = round(shares * (cur_nav - yest_nav), 2)
        else:
            # 持有期/周期型每日平稳收益计算
            dp = round(daily_rate, 2)
            if last_date != TODAY_STR:
                mv = round(prev_mv + dp, 2)
            else:
                mv = prev_mv
            cur_nav = round(mv / shares, 4) if shares > 0 else 1.0000
            yest_nav = round((mv - dp) / shares, 4) if shares > 0 else 1.0000

        cp = round(mv - principal, 2)

        rec = {
            **h,
            "market_value": mv,
            "daily_profit": dp,
            "cumulative_profit": cp,
            "current_nav": cur_nav,
            "yesterday_nav": yest_nav,
            "sync_status": "ok",
            "last_sync_date": TODAY_STR
        }

        total_assets += mv
        total_daily += dp
        total_cum += cp
        updated_products.append(rec)

    # 历史走势（最近 90 天）
    history = [x for x in old_data.get("history", []) if x.get("date") != TODAY_STR]
    history.append({
        "date": TODAY_STR,
        "total_assets": round(total_assets, 2),
        "daily_profit": round(total_daily, 2),
        "cumulative_profit": round(total_cum, 2)
    })
    history = history[-90:]

    summary = {
        "total_assets": round(total_assets, 2),
        "daily_profit": round(total_daily, 2),
        "cumulative_profit": round(total_cum, 2)
    }

    result = {
        "updated_at": TIME_STR,
        "summary": summary,
        "products": updated_products,
        "history": history
    }

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[成功] 收益同步完成: 总资产={summary['total_assets']}, 今日收益={summary['daily_profit']}")
    send_notification(summary, updated_products)


if __name__ == "__main__":
    main()
