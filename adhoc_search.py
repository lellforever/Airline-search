#!/usr/bin/env python3
"""
按需查询 (ad-hoc) — 读取 queries.json, 逐条查 seats.aero, 结果写到 stdout (workflow 存为 adhoc.txt)
不过滤计划/价格: 列出所有计划的超经/商务/头等(可选经济), 标出座位够不够全家
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

API_BASE = "https://seats.aero/partnerapi"
API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")
MAX_PAGES = 3

MR_PARTNERS = {
    "aeroplan", "virginatlantic", "flyingblue", "singapore",
    "qantas", "delta", "etihad", "emirates", "qatar",
}
CABIN_FIELDS = {
    "Y": ("YAvailable", "YMileageCost", "YRemainingSeats", "YDirect"),
    "W": ("WAvailable", "WMileageCost", "WRemainingSeats", "WDirect"),
    "J": ("JAvailable", "JMileageCost", "JRemainingSeats", "JDirect"),
    "F": ("FAvailable", "FMileageCost", "FRemainingSeats", "FDirect"),
}
CABIN_NAMES = {"Y": "经济", "W": "超经", "J": "商务", "F": "头等"}


def channel_of(program):
    if program == "united":
        return "UA里程"
    if program in MR_PARTNERS:
        return "MR转点"
    return "其他计划"


def fetch(origins, dests, start, end):
    params = {
        "origin_airport": ",".join(origins),
        "destination_airport": ",".join(dests),
        "start_date": start,
        "end_date": end,
        "take": 1000,
    }
    records, cursor, remaining = [], None, "?"
    for _ in range(MAX_PAGES):
        p = dict(params)
        if cursor is not None:
            p["cursor"] = cursor
        resp = requests.get(
            f"{API_BASE}/search", params=p,
            headers={"Partner-Authorization": API_KEY,
                     "Accept": "application/json"},
            timeout=90,
        )
        resp.raise_for_status()
        body = resp.json()
        remaining = resp.headers.get("x-ratelimit-remaining", "?")
        batch = body.get("data", [])
        records.extend(batch)
        cursor = body.get("cursor")
        if not body.get("hasMore") or not batch or cursor is None:
            break
    return records, remaining


def to_hits(records, cabins):
    best = {}
    for rec in records:
        program = rec.get("Source", "")
        route = rec.get("Route", {})
        rstr = f'{route.get("OriginAirport", "")}-{route.get("DestinationAirport", "")}'
        for cabin in cabins:
            avail_f, cost_f, seats_f, direct_f = CABIN_FIELDS[cabin]
            if not rec.get(avail_f):
                continue
            try:
                miles = int(rec.get(cost_f) or 0)
            except (TypeError, ValueError):
                miles = 0
            try:
                seats = int(rec.get(seats_f) or 0)
            except (TypeError, ValueError):
                seats = 0
            h = {
                "date": rec.get("Date", ""), "route": rstr, "cabin": cabin,
                "program": program, "miles": miles, "seats": seats,
                "direct": bool(rec.get(direct_f)), "channel": channel_of(program),
            }
            key = (h["date"], rstr, cabin, program)
            if key not in best or seats > best[key]["seats"]:
                best[key] = h
    return list(best.values())


def fmt(h, need):
    mark = "✅" if h["seats"] >= need else ("⚠️" if h["seats"] == 0 else "  ")
    miles = f'{h["miles"]:,}' if h["miles"] else "?"
    seats = str(h["seats"]) if h["seats"] else "?"
    return (f'{mark} {h["date"]:<12}{h["route"]:<10}{CABIN_NAMES[h["cabin"]]:<4}'
            f'{h["program"]:<16}{miles:>9}  {seats:>2}座 '
            f'{"直飞" if h["direct"] else "转机"} [{h["channel"]}]')


def run_query(q):
    name = q.get("name", "未命名")
    origins, dests = q["from"], q["to"]
    cabins = q.get("cabins", ["W", "J", "F"])
    need = int(q.get("min_seats", 4))
    print("\n" + "=" * 72)
    print(f"🔎 {name}: {','.join(origins)} → {','.join(dests)}  "
          f"{q['start']} ~ {q['end']}  舱位 {'/'.join(CABIN_NAMES[c] for c in cabins)}  需 {need} 座")
    print("-" * 72)
    try:
        records, remaining = fetch(origins, dests, q["start"], q["end"])
    except Exception as e:
        print(f"  请求失败: {e}")
        return
    hits = to_hits(records, cabins)
    print(f"  原始记录 {len(records)} 条 | API 今日剩余: {remaining}")
    if not hits:
        print("  (没有任何奖励舱 — 这个窗口/航线基本没戏, 或航司还没放票)")
        return
    hits.sort(key=lambda h: (h["cabin"], h["miles"] or 10**9, h["date"]))
    # 摘要: 每个舱位, 座位够的最低价
    for cabin in cabins:
        ok = [h for h in hits if h["cabin"] == cabin and h["seats"] >= need and h["miles"]]
        anyc = [h for h in hits if h["cabin"] == cabin and h["miles"]]
        if ok:
            b = min(ok, key=lambda h: h["miles"])
            print(f"  ▶ {CABIN_NAMES[cabin]}: 够 {need} 座的最低 {b['miles']:,} "
                  f"({b['program']}, {b['date']}, {b['route']}), 共 {len(ok)} 个可选")
        elif anyc:
            b = min(anyc, key=lambda h: h["miles"])
            print(f"  ▶ {CABIN_NAMES[cabin]}: 没有够 {need} 座的; 最低单座价 {b['miles']:,} "
                  f"({b['program']}, {b['seats'] or '?'}座)")
        else:
            print(f"  ▶ {CABIN_NAMES[cabin]}: 无")
    print()
    for h in hits:
        print(fmt(h, need))


def main():
    print(f"运行时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    if not API_KEY:
        sys.exit("错误: 请先设置环境变量 SEATS_AERO_API_KEY")
    with open("queries.json", encoding="utf-8") as f:
        cfg = json.load(f)
    for q in cfg.get("queries", []):
        run_query(q)
    print("\n✅=座位够全家  ⚠️=座位数未知  [MR转点]=Amex可转  [UA里程]=United可用")
    print("提示: 结果是 seats.aero 缓存, 出票前到航司官网确认。")


if __name__ == "__main__":
    main()
