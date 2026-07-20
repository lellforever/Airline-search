#!/usr/bin/env python3
"""
Award flight auto-search via seats.aero Partner API
---------------------------------------------------
用法:
  1. 订阅 seats.aero Pro ($9.99/月), 在 Settings -> API 生成 API key
  2. export SEATS_AERO_API_KEY="你的key"
  3. python3 award_search.py
  4. (可选) 加进 crontab 每天自动跑:
     0 8 * * * /usr/bin/python3 /path/to/award_search.py >> ~/award_log.txt

依赖: pip install requests
"""

import os
import sys
from datetime import date, timedelta

import requests

API_BASE = "https://seats.aero/partnerapi"
API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")

# ============ 配置区:改这里 ============

# 想搜的航线 (出发, 到达) — 支持机场码
ROUTES = [
    ("LAX", "HKG"),
    ("LAX", "TPE"),
    ("LAX", "NRT"),
    ("SFO", "HKG"),
]

# 搜索日期范围
START_DATE = date.today() + timedelta(days=14)
END_DATE = date.today() + timedelta(days=120)

# 舱位: Y=经济 W=超经 J=商务 F=头等
CABINS = ["J", "F"]

# 只看 Amex MR 转点伙伴 (设为 None 则显示全部计划)
MR_PARTNERS = {
    "aeroplan",        # Air Canada Aeroplan (换ANA/星盟很好用)
    "virginatlantic",  # Virgin Atlantic (换ANA头等的经典渠道)
    "flyingblue",      # Air France/KLM
    "singapore",       # 新航 KrisFlyer
    "qantas",          # 澳航
    "delta",
    "etihad",
    "emirates",
    "qatar",           # 卡塔尔 Avios
}

# 里程价格上限过滤 (超过就不显示, 设 None 不过滤)
MAX_MILES = 130000

# ======================================


def search_route(origin: str, destination: str) -> list:
    """查询单条航线的缓存奖励票数据"""
    params = {
        "origin_airport": origin,
        "destination_airport": destination,
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "take": 500,
    }
    resp = requests.get(
        f"{API_BASE}/search",
        params=params,
        headers={
            "Partner-Authorization": API_KEY,
            "Accept": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    remaining = resp.headers.get("x-ratelimit-remaining", "?")
    print(f"  [API 今日剩余额度: {remaining}]")
    return resp.json().get("data", [])


def extract_hits(records: list) -> list:
    """从返回数据里筛出符合条件的舱位"""
    hits = []
    cabin_fields = {
        "Y": ("YAvailable", "YMileageCost", "YRemainingSeats", "YDirect"),
        "W": ("WAvailable", "WMileageCost", "WRemainingSeats", "WDirect"),
        "J": ("JAvailable", "JMileageCost", "JRemainingSeats", "JDirect"),
        "F": ("FAvailable", "FMileageCost", "FRemainingSeats", "FDirect"),
    }
    for rec in records:
        program = rec.get("Source", "")
        if MR_PARTNERS is not None and program not in MR_PARTNERS:
            continue
        for cabin in CABINS:
            avail_f, cost_f, seats_f, direct_f = cabin_fields[cabin]
            if not rec.get(avail_f):
                continue
            try:
                miles = int(rec.get(cost_f) or 0)
            except (TypeError, ValueError):
                miles = 0
            if MAX_MILES and miles and miles > MAX_MILES:
                continue
            hits.append({
                "date": rec.get("Date", ""),
                "route": f'{rec.get("Route", {}).get("OriginAirport", "")}-'
                         f'{rec.get("Route", {}).get("DestinationAirport", "")}',
                "cabin": cabin,
                "program": program,
                "miles": miles,
                "seats": rec.get(seats_f) or "?",
                "direct": "直飞" if rec.get(direct_f) else "转机",
            })
    return hits


def main():
    if not API_KEY:
        sys.exit("错误: 请先设置环境变量 SEATS_AERO_API_KEY")

    all_hits = []
    for origin, dest in ROUTES:
        print(f"\n搜索 {origin} -> {dest} ({START_DATE} 至 {END_DATE}) ...")
        try:
            records = search_route(origin, dest)
        except requests.HTTPError as e:
            print(f"  请求失败: {e}")
            continue
        hits = extract_hits(records)
        print(f"  找到 {len(hits)} 个符合条件的结果")
        all_hits.extend(hits)

    if not all_hits:
        print("\n没有找到符合条件的奖励票。")
        return

    all_hits.sort(key=lambda h: (h["date"], h["miles"] or 10**9))

    cabin_names = {"Y": "经济", "W": "超经", "J": "商务", "F": "头等"}
    print(f"\n{'日期':<12}{'航线':<10}{'舱位':<6}{'计划':<16}"
          f"{'里程':>9}  {'座位':<5}{'直飞':<4}")
    print("-" * 66)
    for h in all_hits:
        miles_str = f'{h["miles"]:,}' if h["miles"] else "?"
        print(f'{h["date"]:<12}{h["route"]:<10}'
              f'{cabin_names[h["cabin"]]:<6}{h["program"]:<16}'
              f'{miles_str:>9}  {str(h["seats"]):<5}{h["direct"]:<4}')

    print(f"\n共 {len(all_hits)} 条。到 seats.aero 或对应里程计划官网确认后出票。")


if __name__ == "__main__":
    main()
