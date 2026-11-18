#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动理财收益同步与计算脚本
- 支持净值型 (nav) 与 万份收益型 (ten_thousand)
- 多重接口抓取与兜底保护
- 自动更新 data.json 并保留历史走势
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
    "Referer": "https://www.chinawealth.com.cn/zzlc/jsp/lccp.jsp",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


def http_post_json(url, data_dict, timeout=12):
    """通用的 HTTP POST 请求工具，优先使用 requests，无 requests 时降级到 urllib"""
    try:
        import requests
        resp = requests.post(url, data=data_dict, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except ImportError:
        pass
    except Exception as e:
        print(f"requests POST 异常: {e}")

    # Fallback to urllib.request
    try:
        import urllib.request
        import urllib.parse
        encoded_data = urllib.parse.urlencode(data_dict).encode("utf-8")
        req = urllib.request.Request(url, data=encoded_data, headers=HEADERS)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8", errors="ignore")
                return json.loads(body)
    except Exception as e:
        print(f"urllib POST 异常: {e}")
    return None


def fetch_chinawealth_metric(product_code, product_type):
    """
    通过中国理财网公开接口查询最新净值或万份收益
    """
    url = "https://www.chinawealth.com.cn/lcjy/productSearch"
    payload = {"cpdjbm": product_code, "pagenum": 1, "pagesize": 5}
    try:
        data = http_post_json(url, payload)
        if not data:
            return None
        records = data.get("List") or data.get("list") or []
        if not records:
            return None
        item = records[0]
        if product_type == "nav":
            val = item.get("dwjz") or item.get("cpdwjz")
            return float(val) if val else None
        else:
            val = item.get("wfsy") or item.get("mrwfsy")
            return float(val) if val else None
    except Exception as e:
        print(f"抓取中国理财网 [{product_code}] 异常: {e}")
        return None


def fetch_latest_metric(product):
    """
    统一入口：获取产品指标，支持扩展银行专属接口
    """
    code = product.get("code", "").strip()
    ptype = product.get("type", "nav")
    
    # 1. 尝试理财网统一公开接口
    val = fetch_chinawealth_metric(code, ptype)
    if val is not None:
        return val

    # 若接口未返回，返回 None，由主流程使用历史缓存兜底，不中断流程
    return None


def send_notification(summary, products):
    """可选的微信消息推送 (通过 GitHub Secrets 配置)"""
    serverchan_key = os.environ.get("SERVERCHAN_KEY")
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN")

    if not serverchan_key and not pushplus_token:
        return

    title = f"理财收益播报：今日 {summary['daily_profit']:+.2f} 元"
    content_lines = [
        f"### 📊 今日理财汇总 ({TODAY_STR})",
        f"- **总持有资产**: ¥{summary['total_assets']:,.2f}",
        f"- **今日单日收益**: ¥{summary['daily_profit']:+,.2f}",
        f"- **累计总收益**: ¥{summary['cumulative_profit']:+,.2f}",
        "",
        "### 🏦 产品明细",
    ]
    for p in products:
        content_lines.append(
            f"- **{p['bank']} · {p['name']}**: 市值 ¥{p['market_value']:,.2f} | "
            f"今日: {p['daily_profit']:+,.2f} 元 | 累计: {p['cumulative_profit']:+,.2f} 元"
        )
    content = "\n".join(content_lines)

    try:
        if serverchan_key:
            import urllib.request
            import urllib.parse
            url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
            data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
            print("[通知] Server酱推送成功")
    except Exception as e:
        print(f"[通知] Server酱推送异常: {e}")

    try:
        if pushplus_token:
            import urllib.request
            url = "http://www.pushplus.plus/send"
            payload = json.dumps({"token": pushplus_token, "title": title, "content": content, "template": "markdown"}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            print("[通知] PushPlus 推送成功")
    except Exception as e:
        print(f"[通知] PushPlus 推送异常: {e}")


def main():
    holdings_path = "holdings.json"
    data_path = "data.json"

    if not os.path.exists(holdings_path):
        print(f"[错误] 未找到持仓配置文件: {holdings_path}")
        sys.exit(1)

    with open(holdings_path, "r", encoding="utf-8") as f:
        holdings = json.load(f)

    # 读取原有缓存数据
    old_data = {"products": [], "history": []}
    if os.path.exists(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception as e:
            print(f"[警告] 读取旧 data.json 失败，将重新构建: {e}")

    old_map = {p["id"]: p for p in old_data.get("products", [])}

    updated_products = []
    total_assets = 0.0
    total_daily = 0.0
    total_cum = 0.0

    for h in holdings:
        pid = h["id"]
        ptype = h.get("type", "nav")
        old_p = old_map.get(pid, {})
        val = fetch_latest_metric(h)

        if ptype == "nav":
            # 净值型
            init_nav = float(h.get("initial_nav") or 1.0)
            prev_nav = old_p.get("current_nav", init_nav)

            if val is not None:
                cur_nav = round(val, 4)
                # 若抓到新净值且与上一期不同，昨日净值置为旧净值
                yest_nav = prev_nav if val != prev_nav else old_p.get("yesterday_nav", cur_nav)
            else:
                # 抓取失败或非披露日，沿用已有净值
                cur_nav = prev_nav
                yest_nav = old_p.get("yesterday_nav", cur_nav)

            mv = round(h["shares"] * cur_nav, 2)
            # 首日无昨日变动时日收益算为0，避免将建仓以来的涨幅误判为单日收益
            if old_p.get("current_nav") is None:
                dp = 0.0
            else:
                dp = round(h["shares"] * (cur_nav - yest_nav), 2)
            cp = round(mv - h["principal"], 2)

            rec = {
                **h,
                "current_nav": cur_nav,
                "yesterday_nav": yest_nav,
                "market_value": mv,
                "daily_profit": dp,
                "cumulative_profit": cp,
                "sync_status": "ok" if val is not None else "cached",
                "last_sync_date": TODAY_STR if val is not None else old_p.get("last_sync_date", TODAY_STR)
            }
        else:
            # 现金管理型 / 万份收益型
            ten_k = val if val is not None else old_p.get("ten_k_return", 0.0)
            amt = old_p.get("market_value", h["principal"])
            dp = round((amt / 10000.0) * ten_k, 2)

            # 防重复累加：仅当今天未计入且有新收益时累加到本金
            last_date = old_p.get("last_sync_date")
            if last_date != TODAY_STR and val is not None:
                amt = round(amt + dp, 2)
                last_date = TODAY_STR

            cp = round(amt - h["principal"], 2)

            rec = {
                **h,
                "ten_k_return": ten_k,
                "market_value": amt,
                "daily_profit": dp,
                "cumulative_profit": cp,
                "sync_status": "ok" if val is not None else "cached",
                "last_sync_date": last_date or TODAY_STR
            }

        total_assets += rec["market_value"]
        total_daily += rec["daily_profit"]
        total_cum += rec["cumulative_profit"]
        updated_products.append(rec)

    # 历史走势（按天更新，若当日已存在则覆盖）
    history = [x for x in old_data.get("history", []) if x.get("date") != TODAY_STR]
    history.append({
        "date": TODAY_STR,
        "total_assets": round(total_assets, 2),
        "daily_profit": round(total_daily, 2),
        "cumulative_profit": round(total_cum, 2)
    })
    # 保留最近 90 天
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

    # 触发通知
    send_notification(summary, updated_products)


if __name__ == "__main__":
    main()
