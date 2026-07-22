#!/usr/bin/env python3
"""
Award flight auto-search v5 — seats.aero Partner API
广搜: 精选过滤 (商务/头等 saver) 当行情参考
假期窗口 (去程3/24-26, 回程4/4前后): 超经/商务/头等全计划不过滤, 交给 Claude 分析
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

API_BASE = "https://seats.aero/partnerapi"
API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")

# ============ 配置区 ============

ORIGINS = ["LAX", "SFO"]

DEST_GROUPS = [
    ("亚洲", ["HND", "NRT", "HKG", "TPE", "ICN", "SIN", "BKK"], True),
    ("欧洲", ["LHR", "CDG", "FRA", "AMS", "ZRH", "MUC"], True),
    ("大洋洲", ["SYD", "MEL", "AKL"], True),
    ("海岛(可转机)", ["MLE", "NAN", "PPT", "TVU"], False),
    ("马尔代夫通道", ["DOH", "DXB", "AUH"], False),
]

START_DATE = date.today() + timedelta(days=14)
END_DATE = date.today() + timedelta(days=330)

CABINS = ["J", "F"]                      # 广搜只看商务+头等
RAW_CABINS = ["W", "J", "F"]             # 假期窗口: 超经+商务+头等, 不看经济
MAX_MILES = {"J": 140000, "F": 200000}   # 仅用于广搜的saver判断
MIN_SEATS_STAR = 4
MIN_DISTANCE_MI = 3500                   # 广搜过滤短途; 假期窗口不过滤

# 假期行程
OUT_START = date(2027, 3, 24)
OUT_END = date(2027, 3, 26)
RET_START = date(2027, 4, 3)
RET_END = date(2027, 4, 5)
RETURN_TO = ["LAX"]

MAX_PAGES = 8

MR_PARTNERS = {
    "aeroplan", "virginatlantic", "flyingblue", "singapore",
    "qantas", "delta", "etihad", "emirates", "qatar",
}
WATCH_PROGRAMS = MR_PARTNERS | {"united"}

# ================================

CABIN_FIELDS = {
    "Y": ("YAvailable", "YMileageCost", "YRemainingSeats", "YDirect"),
    "W": ("WAvailable", "WMileageCost", "WRemainingSeats", "WDirect"),
    "J": ("JAvailable", "JMileageCost", "JRemainingSeats", "JDirect"),
    "F": ("FAvailable", "FMileageCost", "FRemainingSeats", "FDirect"),
}
CABIN_NAMES = {"Y": "经济", "W": "超经", "J": "商务", "F": "头等"}


def search_group(origins, dests, start, end):
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


def channel_of(program):
    if program == "united":
        return "UA里程"
    if program in MR_PARTNERS:
        return "MR转点"
    return "其他计划"


def extract_hits(records, direct_only, ptag=None, raw=False):
    """raw=True: 假期窗口模式, 超经/商务/头等全计划不过滤"""
    hits = []
    cabins = RAW_CABINS if raw else CABINS
    for rec in records:
        program = rec.get("Source", "")
        if not raw and program not in WATCH_PROGRAMS:
            continue
        for cabin in cabins:
            avail_f, cost_f, seats_f, direct_f = CABIN_FIELDS[cabin]
            if not rec.get(avail_f):
                continue
            is_direct = bool(rec.get(direct_f))
            if not raw and direct_only and not is_direct:
                continue
            try:
                miles = int(rec.get(cost_f) or 0)
            except (TypeError, ValueError):
                miles = 0
            if not raw and miles and miles > MAX_MILES.get(cabin, 10**9):
                continue
            try:
                seats = int(rec.get(seats_f) or 0)
            except (TypeError, ValueError):
                seats = 0
            route = rec.get("Route", {})
            try:
                dist = int(route.get("Distance") or 0)
            except (TypeError, ValueError):
                dist = 0
            if not raw and 0 < dist < MIN_DISTANCE_MI:
                continue
            hits.append({
                "date": rec.get("Date", ""),
                "route": f'{route.get("OriginAirport", "")}-'
                         f'{route.get("DestinationAirport", "")}',
                "cabin": cabin,
                "program": program,
                "miles": miles,
                "seats": seats,
                "direct": is_direct,
                "star": seats >= MIN_SEATS_STAR,
                "channel": channel_of(program),
                "priority": ptag,
            })
    return hits


def fmt_line(h):
    star = "★" if h["star"] else " "
    tag = "【" + h["priority"] + "】" if h.get("priority") else ""
    miles_str = f'{h["miles"]:,}' if h["miles"] else "?"
    seats_str = str(h["seats"]) if h["seats"] else "?"
    direct_str = "直飞" if h["direct"] else "转机"
    return (f'{star} {h["date"]:<12}{h["route"]:<10}{CABIN_NAMES[h["cabin"]]:<4}'
            f'{h["program"]:<16}{miles_str:>9}  {seats_str:>2}座 '
            f'{direct_str} [{h["channel"]}]{tag}')


def dedupe(hits):
    best = {}
    for h in hits:
        key = (h["date"], h["route"], h["cabin"], h["program"])
        if key not in best or h["seats"] > best[key]["seats"]:
            tag = best[key].get("priority") if key in best else None
            best[key] = h
            if not h.get("priority") and tag:
                h["priority"] = tag
        elif h.get("priority") and not best[key].get("priority"):
            best[key]["priority"] = h["priority"]
    return list(best.values())


def main():
    print(f"运行时间: "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    if not API_KEY:
        sys.exit("错误: 请先设置环境变量 SEATS_AERO_API_KEY")

    all_hits = []
    for group_name, dests, direct_only in DEST_GROUPS:
        print(f"\n搜索 {'/'.join(ORIGINS)} -> {group_name} "
              f"({START_DATE} 至 {END_DATE}) ...")
        try:
            records = search_group(ORIGINS, dests, START_DATE, END_DATE)
            hits = extract_hits(records, direct_only)
            print("  + 去程专项 (3/24-26 出发, 全数据不过滤) ...")
            rec_out = search_group(ORIGINS, dests, OUT_START, OUT_END)
            hits += extract_hits(rec_out, direct_only, ptag="去程", raw=True)
            print("  + 回程专项 (4/4 前后回 LAX, 全数据不过滤) ...")
            rec_ret = search_group(dests, RETURN_TO, RET_START, RET_END)
            hits += extract_hits(rec_ret, direct_only, ptag="回程", raw=True)
        except Exception as e:
            print(f"  请求失败, 跳过该组: {e}")
            continue
        print(f"  找到 {len(hits)} 个符合条件的结果")
        all_hits.extend(hits)

    if not all_hits:
        print("\n没有找到符合条件的奖励票。")
        return

    all_hits = dedupe(all_hits)
    all_hits.sort(key=lambda h: (not h.get("priority"), h["date"],
                                 h["route"], h["miles"] or 10**9))
    outbound = [h for h in all_hits if h.get("priority") == "去程"]
    inbound = [h for h in all_hits if h.get("priority") == "回程"]
    starred = [h for h in all_hits if h["star"] and not h.get("priority")]

    print("\n" + "=" * 70)
    print(f"🎯 去程 {OUT_START} 至 {OUT_END} (LAX/SFO 出发, 未筛选全数据): "
          f"{len(outbound)} 条")
    print("-" * 70)
    if outbound:
        for h in outbound:
            print(fmt_line(h))
    else:
        print("(去程窗口暂无数据, 持续监控中)")

    print(f"\n🎯 回程 {RET_START} 至 {RET_END} (目的地 -> LAX, 未筛选全数据): "
          f"{len(inbound)} 条")
    print("-" * 70)
    if inbound:
        for h in inbound:
            print(fmt_line(h))
    else:
        print("(回程窗口暂无数据, 持续监控中)")

    if starred:
        print(f"\n★ 其他日期精选 (商务/头等saver, >= {MIN_SEATS_STAR} 座): "
              f"{len(starred)} 条")
        print("-" * 70)
        for h in starred:
            print(fmt_line(h))

    print(f"\n说明: [MR转点]=Amex可转; [UA里程]=United余额可用; "
          f"[其他计划]=需该计划里程。")
    print(f"\n共 {len(all_hits)} 条。先到对应计划官网确认舱位仍在, 再转点出票。")


if __name__ == "__main__":
    main()
