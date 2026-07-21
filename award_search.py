#!/usr/bin/env python3
"""
Award flight auto-search v4 — seats.aero Partner API
广搜: 精选过滤 (商务/头等 saver) 当行情参考
假期窗口 (去程3/24-26, 回程4/4前后): 全舱位、全计划、不过滤, 交给 Claude 分析
"""

import os
import sys
from datetime import date, timedelta

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
]

START_DATE = date.today() + timedelta(days=14)
END_DATE = date.today() + timedelta(days=330)

CABINS = ["J", "F"]                      # 广搜只看商务+头等
RAW_CABINS = ["Y", "W", "J", "F"]        # 假期窗口: 全部舱位
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
    """raw=True: 假期窗口模式, 全舱位全计划不过滤"""
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
