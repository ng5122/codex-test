"""Shared snapshot-derived statistics used by the GUI and read-only MCP."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def _snapshots(conn, product_id: str, end: str | None = None):
    sql = "SELECT captured_at,sold,price FROM snapshots WHERE product_id=?"
    args: tuple = (product_id,)
    if end:
        sql += " AND captured_at<=?"
        args += (end,)
    sql += " ORDER BY captured_at"
    rows=[dict(x) for x in conn.execute(sql,args)]
    for row in rows:row["_captured_dt"]=datetime.fromisoformat(row["captured_at"])
    return rows


def high_water_at(rows: list[dict], boundary: datetime) -> int | None:
    vals = []
    for row in rows:
        captured = row.get("_captured_dt") or datetime.fromisoformat(row["captured_at"])
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=TZ)
        if captured <= boundary:
            vals.append(int(row["sold"]))
    return max(vals) if vals else None


def has_snapshot_at(rows: list[dict], boundary: datetime) -> bool:
    for row in rows:
        captured=row.get("_captured_dt") or datetime.fromisoformat(row["captured_at"])
        if captured.tzinfo is None:captured=captured.replace(tzinfo=TZ)
        if captured==boundary:return True
    return False


def delta(current: int | None, baseline: int | None) -> int | None:
    return None if current is None or baseline is None else max(0,current-baseline)


def product_summary(conn, product_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    today0 = datetime.combine(now.date(),time.min,TZ)
    yesterday0 = today0-timedelta(days=1)
    hour0 = now.replace(minute=0,second=0,microsecond=0)
    rows = _snapshots(conn,product_id)
    current = high_water_at(rows,now)
    today_base = high_water_at(rows,today0)
    yesterday_base = high_water_at(rows,yesterday0)
    previous_hour=hour0-timedelta(hours=1)
    previous_hour_base = high_water_at(rows,previous_hour) if has_snapshot_at(rows,previous_hour) else None
    current_hour_base = high_water_at(rows,hour0) if has_snapshot_at(rows,hour0) else None
    # When monitoring began after midnight, report a clearly incomplete day from its first real point.
    today_rows = [r for r in rows if r["_captured_dt"].date()==now.date() and r["_captured_dt"]<=now]
    if today_base is None and today_rows:
        if len(today_rows)>=2:today_base = high_water_at(rows,today_rows[0]["_captured_dt"])
    last = rows[-1] if rows else None
    return {
        "product_id": product_id, "sold": current,
        "today": delta(current,today_base) if len(today_rows)>=2 else None, "today_incomplete": today0.isoformat() not in {r["captured_at"] for r in rows},
        "yesterday": delta(today_base,yesterday_base) if has_snapshot_at(rows,today0) and has_snapshot_at(rows,yesterday0) else None,
        "last_hour": delta(current_hour_base,previous_hour_base),
        "price": last.get("price") if last else None,
        "last_captured_at": last.get("captured_at") if last else None,
        "estimated_revenue": daily_sales(conn,product_id,now.date(),now=now)["estimated_revenue"],
    }


def daily_sales(conn, product_id: str, day: date, now: datetime | None = None) -> dict:
    rows = _snapshots(conn,product_id)
    now=now or datetime.now(TZ)
    start = datetime.combine(day,time.min,TZ)
    next_day = start+timedelta(days=1)
    start_exact=has_snapshot_at(rows,start)
    start_value = high_water_at(rows,start) if start_exact else None
    current_day=day==now.date()
    end_value=high_water_at(rows,now) if current_day else (high_water_at(rows,next_day) if has_snapshot_at(rows,next_day) else None)
    day_rows=[r for r in rows if start<=r["_captured_dt"]<=now] if current_day else []
    if start_value is None and current_day:
        first = [r for r in day_rows if r["_captured_dt"]>start]
        if first:
            if len(day_rows)>=2:start_value = high_water_at(rows,day_rows[0]["_captured_dt"])
    hours=hourly_sales(rows,day); sales=delta(end_value,start_value)
    if current_day and len(day_rows)<2:sales=None
    current_day=day==now.date()
    completed=hours[:now.hour] if current_day else hours
    revenue=None
    if sales is not None and all(x["sales"] is not None and x["price"] is not None for x in completed):
        revenue=sum(x["sales"]*x["price"] for x in completed)
    return {"date":day.isoformat(),"sales":sales,"estimated_revenue":revenue,"hours":hours,"incomplete":not start_exact}


def hourly_sales(rows: list[dict], day: date) -> list[dict]:
    result=[]
    high=None
    # Establish high-water from snapshots strictly before this date.
    midnight=datetime.combine(day,time.min,TZ)
    for hour in range(24):
        start=midnight+timedelta(hours=hour)
        end=start+timedelta(hours=1)
        baseline=high_water_at(rows,start) if has_snapshot_at(rows,start) else None
        value=high_water_at(rows,end) if has_snapshot_at(rows,end) else None
        exact_rows=[r for r in rows if r["_captured_dt"]==end and r.get("price") is not None]
        result.append({"hour":hour,"sales":delta(value,baseline),"price":exact_rows[-1]["price"] if exact_rows else None})
    return result


def shop_ranking(conn, metric="today", now: datetime | None = None) -> list[dict]:
    now=now or datetime.now(TZ)
    products=[dict(r) for r in conn.execute("SELECT * FROM products WHERE active=1")]
    shops={}
    for product in products:
        summary=product_summary(conn,product["id"],now)
        shop=product.get("shop_name") or "（未知店铺）"
        row=shops.setdefault(shop,{"shop_name":shop,"product_count":0,"today":0,"yesterday":0,"last_hour":0,"estimated_revenue":0.0})
        row["product_count"]+=1
        for key in ("today","yesterday","last_hour"):
            if summary[key] is None: row.setdefault(key+"_incomplete",False); row[key+"_incomplete"]=True
            else: row[key]+=summary[key]
        if summary["estimated_revenue"] is None:
            row["estimated_revenue_incomplete"]=True
        else: row["estimated_revenue"]+=summary["estimated_revenue"]
    for row in shops.values():
        for key in ("today","yesterday","last_hour","estimated_revenue"):
            if row.get(key+"_incomplete"):row[key]=None
    return sorted(shops.values(),key=lambda x:x.get(metric) or 0,reverse=True)

