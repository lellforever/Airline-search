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
    ("海岛(可转机)", ["MLE", "NAN", "PPT", "TVU"], False),
]

START_DATE = date.today() + timedelta(days=14)
END_DATE = date.today() + timedelta(days=330)

CABINS = ["J", "F"]                      # 商务 + 头等
MAX_MILES = {"J": 140000, "F": 200000}   # 单程里程上限, 超过视为非saver
MIN_SEATS_STAR = 4                       # 家庭出行: >=4 座标星
MIN_SEATS = 2                            # 少于这个座位数的不要 (过滤1座票)
MIN_DISTANCE_MI = 3500                   # 短于约7小时飞行的不要 (夏威夷等)

# 假期窗口: 这个日期段内的票单独置顶提醒
PRIORITY_START = date(2027, 3, 1)
PRIORITY_END = date(2027, 4, 10)

# API 每页最多返回 1000 条且按日期排序, 必须翻页才能拿到远期数据
MAX_PAGES = 8                            # 每组最多翻的页数 (省API额度; 假期窗口有专项查询兜底)

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


def search_group(origins, dests, start, end):
    """分页拉取: API 每页最多 1000 条且按日期升序, 不翻页就只能看到最近一个月"""
    base_params = {
        "origin_airport": ",".join(origins),
        "destination_airport": ",".join(dests),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "take": 1000,
    }
    records = []
    cursor = None
    remaining = "?"
    for page in range(MAX_PAGES):
        params = dict(base_params)
        if cursor is not None:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{API_BASE}/search",
                params=params,
                headers={"Partner-Authorization": API_KEY,
                         "Accept": "application/json"},
                timeout=90,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            print(f"  [第 {page + 1} 页请求失败, 用已取到的数据继续: {e}]")
            break
        remaining = resp.headers.get("x-ratelimit-remaining", "?")
        batch = body.get("data", [])
        records.extend(batch)
        if not body.get("hasMore") or not batch:
            break
        cursor = body.get("cursor")
        if cursor is None:
            break
    last_date = records[-1].get("Date", "?") if records else "-"
    print(f"  [翻了 {page + 1} 页, 累计 {len(records)} 条原始记录, "
          f"覆盖到 {last_date} | API 今日剩余额度: {remaining}]")
    return records


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
            if 0 < seats < MIN_SEATS:
                continue                     # 座位数已知且太少, 不要
            route = rec.get("Route", {})
            try:
                dist = int(route.get("Distance") or 0)
            except (TypeError, ValueError):
                dist = 0
            if 0 < dist < MIN_DISTANCE_MI:
                continue                     # 航距太短 (约<7小时), 不要
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
    tag = "【" + h["priority"] + "】" if h.get("priority") else ""
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
            records = search_group(ORIGINS, dests, START_DATE, END_DATE)
            print("  + 假期窗口专项补查 ...")
            records += search_group(ORIGINS, dests, PRIORITY_START, PRIORITY_END)
        except Exception as e:
            print(f"  请求失败, 跳过该组: {e}")
            continue
        hits = extract_hits(records, direct_only)
        print(f"  找到 {len(hits)} 个符合条件的结果")
        all_hits.extend(hits)

    if not all_hits:
        print("\n没有找到符合条件的奖励票。")
        return

    # 去重: 翻页会有重叠, 同一 (日期,航线,舱位,计划) 只保留座位最多的一条
    best = {}
    for h in all_hits:
        key = (h["date"], h["route"], h["cabin"], h["program"])
        if key not in best or h["seats"] > best[key]["seats"]:
            best[key] = h
    all_hits = list(best.values())

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

    FULL_LIST_CAP = 200
    print(f"\n全部结果 (最多显示 {FULL_LIST_CAP} 条):")
    print("-" * 70)
    for h in all_hits[:FULL_LIST_CAP]:
        print(fmt_line(h))
    if len(all_hits) > FULL_LIST_CAP:
        print(f"... 另有 {len(all_hits) - FULL_LIST_CAP} 条未显示 "
              f"(完整数据可在 Actions 日志或 seats.aero 查看)")

    print(f"\n说明: [MR转点]=Amex积分转对应计划出票; [UA里程]=用你的United余额,"
          f"\n持United信用卡在官网登陆后部分saver有卡友折扣价, 以官网显示为准。")
    print(f"\n共 {len(all_hits)} 条。先到对应计划官网确认舱位仍在, 再转点出票。")


if __name__ == "__main__":
    main()
