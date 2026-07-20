#!/usr/bin/env python3
"""
Award flight auto-search v2 — seats.aero Partner API
LAX/SFO 出发 商务/头等 saver 监控:
  - 直飞: 亚洲/欧洲/大洋洲主要城市
  - 可转机: 海岛目的地 (马尔代夫/斐济/大溪地等)
  - 4 张票优先标星, MR 转点伙伴 + United 里程分别标注
"""

import os
import sys
from datetime import date, timedelta

import requests

API_BASE = "https://seats.aero/partnerapi"
API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")

# ============ 配置区 ============

ORIGINS = ["LAX", "SFO"]

# 目的地分组: (组名, 机场列表, 是否只看直飞)
DEST_GROUPS = [
    ("亚洲", ["HND", "NRT", "HKG", "TPE", "ICN", "SIN", "BKK"], True),
    ("欧洲", ["LHR", "CDG", "FRA", "AMS", "ZRH", "MUC"], True),
    ("大洋洲", ["SYD", "MEL", "AKL"], True),
    ("海岛(可转机)", ["MLE", "NAN", "PPT", "HNL", "TVU"], False),
]

START_DATE = date.today() + timedelta(days=14)
END_DATE = date.today() + timedelta(days=330)

CABINS = ["J", "F"]                      # 商务 + 头等
MAX_MILES = {"J": 140000, "F": 200000}   # 单程里程上限, 超过视为非saver
MIN_SEATS_STAR = 4                       # 家庭出行: >=4 座标星

# 假期窗口: 这个日期段内的票单独置顶提醒
PRIORITY_START = date(2027, 3, 1)
PRIORITY_END = date(2027, 4, 10)

# MR 转点伙伴 + United (你有 UA 里程)
MR_PARTNERS = {
    "aeroplan", "virginatlantic", "flyingblue", "singapore",
    "qantas", "delta", "etihad", "emirates", "qatar",
}
WATCH_PROGRAMS = MR_PARTNERS | {"united"}

# ================================

CABIN_FIELDS = {
    "J": ("JAvailable", "JMileageCost", "JRemainingSeats", "JDirect"),
    "F": ("FAvailable", "FMileageCost", "FRemainingSeats", "FDirect"),
}
CABIN_NAMES = {"J": "商务", "F": "头等"}


def search_group(origins, dests):
    params = {
        "origin_airport": ",".join(origins),
        "destination_airport": ",".join(dests),
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "take": 1000,
    }
    resp = requests.get(
        f"{API_BASE}/search",
        params=params,
        headers={"Partner-Authorization": API_KEY, "Accept": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    print(f"  [API 今日剩余额度: {resp.headers.get('x-ratelimit-remaining', '?')}]")
    return resp.json().get("data", [])


def _in_window(date_str):
    try:
        d = date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return False
    return PRIORITY_START <= d <= PRIORITY_END


def extract_hits(records, direct_only):
    hits = []
    for rec in records:
        program = rec.get("Source", "")
        if program not in WATCH_PROGRAMS:
            continue
        for cabin in CABINS:
            avail_f, cost_f, seats_f, direct_f = CABIN_FIELDS[cabin]
            if not rec.get(avail_f):
                continue
            is_direct = bool(rec.get(direct_f))
            if direct_only and not is_direct:
                continue
            try:
                miles = int(rec.get(cost_f) or 0)
            except (TypeError, ValueError):
                miles = 0
            if miles and miles > MAX_MILES[cabin]:
                continue
            try:
                seats = int(rec.get(seats_f) or 0)
            except (TypeError, ValueError):
                seats = 0
            route = rec.get("Route", {})
            hits.append({
                "date": rec.get("Date", ""),
                "route": f'{route.get("OriginAirport", "")}-{route.get("DestinationAirport", "")}',
                "cabin": cabin,
                "program": program,
                "miles": miles,
                "seats": seats,
                "direct": is_direct,
                "star": seats >= MIN_SEATS_STAR,
                "channel": "UA里程" if program == "united" else "MR转点",
                "priority": _in_window(rec.get("Date", "")),
            })
    return hits


def fmt_line(h):
    star = "★" if h["star"] else " "
    tag = "【假期】" if h.get("priority") else ""
    miles_str = f'{h["miles"]:,}' if h["miles"] else "?"
    seats_str = str(h["seats"]) if h["seats"] else "?"
    direct_str = "直飞" if h["direct"] else "转机"
    return (f'{star} {h["date"]:<12}{h["route"]:<10}{CABIN_NAMES[h["cabin"]]:<4}'
            f'{h["program"]:<15}{miles_str:>9}  {seats_str:>2}座 '
            f'{direct_str} [{h["channel"]}]{tag}')


def main():
    if not API_KEY:
        sys.exit("错误: 请先设置环境变量 SEATS_AERO_API_KEY")

    all_hits = []
    for group_name, dests, direct_only in DEST_GROUPS:
        print(f"\n搜索 {'/'.join(ORIGINS)} -> {group_name} "
              f"({START_DATE} 至 {END_DATE}) ...")
        try:
            records = search_group(ORIGINS, dests)
        except requests.HTTPError as e:
            print(f"  请求失败: {e}")
            continue
        hits = extract_hits(records, direct_only)
        print(f"  找到 {len(hits)} 个符合条件的结果")
        all_hits.extend(hits)

    if not all_hits:
        print("\n没有找到符合条件的奖励票。")
        return

    all_hits.sort(key=lambda h: (not h.get("priority"), not h["star"],
                                 h["date"], h["miles"] or 10**9))
    priority = [h for h in all_hits if h.get("priority")]
    starred = [h for h in all_hits if h["star"] and not h.get("priority")]

    print("\n" + "=" * 70)
    print(f"🎯 假期窗口 {PRIORITY_START} 至 {PRIORITY_END}: {len(priority)} 条")
    print("-" * 70)
    if priority:
        for h in priority:
            print(fmt_line(h))
    else:
        print("(暂时没有假期窗口内的票, 各计划仍在陆续放票, 持续监控中)")

    if starred:
        print(f"\n★ 其他日期 >= {MIN_SEATS_STAR} 座: {len(starred)} 条")
        print("-" * 70)
        for h in starred:
            print(fmt_line(h))

    print("\n全部结果:")
    print("-" * 70)
    for h in all_hits:
        print(fmt_line(h))

    print(f"\n说明: [MR转点]=Amex积分转对应计划出票; [UA里程]=用你的United余额,"
          f"\n持United信用卡在官网登陆后部分saver有卡友折扣价, 以官网显示为准。")
    print(f"\n共 {len(all_hits)} 条。先到对应计划官网确认舱位仍在, 再转点出票。")


if __name__ == "__main__":
    main()
