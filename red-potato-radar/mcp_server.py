"""Read-only stdio MCP server for the local monitor database."""
from __future__ import annotations
import io
import os
import sys
from datetime import date, datetime, timedelta
from analytics import daily_sales, product_summary, shop_ranking
from database import Database, TZ

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # Give a useful error when launched outside the packaged environment.
    raise SystemExit("缺少 MCP SDK，请先安装 requirements.txt") from exc

mcp=FastMCP("xhs-sales-monitor")
try:db=Database(read_only=True)
except FileNotFoundError as exc:raise SystemExit(f"红薯雷达 MCP：{exc}。请先运行一次桌面程序创建数据库。") from None

@mcp.tool()
def list_shops() -> dict:
    """List monitored shops with item counts and snapshot-derived sales."""
    conn=db.connect()
    try: return {"as_of":datetime.now(TZ).isoformat(),"basis":"累计已售快照差值；缺失基线为 null","shops":shop_ranking(conn,"today")}
    finally: conn.close()

@mcp.tool()
def list_products(shop: str="", keyword: str="", status: str="active") -> dict:
    """List products filtered by shop, title/ID keyword, and active/delisted status."""
    conn=db.connect()
    try:
        clauses=[]; args=[]
        if status=="active": clauses.append("active=1")
        elif status=="delisted": clauses.append("active=0 AND delisted_at IS NOT NULL")
        if shop: clauses.append("COALESCE(shop_name,'') LIKE ?"); args.append(f"%{shop}%")
        if keyword: clauses.append("(title LIKE ? OR id LIKE ?)"); args += [f"%{keyword}%",f"%{keyword}%"]
        sql="SELECT * FROM products"+(" WHERE "+" AND ".join(clauses) if clauses else "")+" ORDER BY title"
        result=[]
        for p in conn.execute(sql,args): result.append({**dict(p),**product_summary(conn,p["id"])})
        return {"as_of":datetime.now(TZ).isoformat(),"basis":"累计已售快照高水位差值","products":result}
    finally: conn.close()

@mcp.tool()
def get_product_sales(product_id: str, day: str="", days: int=0) -> dict:
    """Get hourly sales for a date or daily sales for the most recent N calendar days."""
    conn=db.connect()
    try:
        if not conn.execute("SELECT 1 FROM products WHERE id=?",(product_id,)).fetchone(): return {"error":"商品不存在"}
        today=datetime.now(TZ).date()
        if day: rows=[daily_sales(conn,product_id,date.fromisoformat(day))]
        else: rows=[daily_sales(conn,product_id,today-timedelta(days=i)) for i in range(max(1,min(days or 1,90))-1,-1,-1)]
        return {"product_id":product_id,"as_of":datetime.now(TZ).isoformat(),"basis":"累计已售高水位相邻快照差值；缺少基线为 null","sales":rows}
    finally: conn.close()

@mcp.tool()
def get_shop_sales(shop: str, day: str="", days: int=0) -> dict:
    """Get a shop's combined hourly sales for a date or daily sales for N days."""
    conn=db.connect()
    try:
        products=[r[0] for r in conn.execute("SELECT id FROM products WHERE active=1 AND shop_name=?",(shop,))]
        if not products: return {"error":"店铺不存在或没有监控商品"}
        dates=[date.fromisoformat(day)] if day else [datetime.now(TZ).date()-timedelta(days=i) for i in range(max(1,min(days or 1,90))-1,-1,-1)]
        sales=[]
        for d in dates:
            vals=[daily_sales(conn,p,d) for p in products]
            hours=[]
            for h in range(24):
                points=[x["hours"][h]["sales"] for x in vals]
                revenues=[(x["hours"][h]["sales"]*x["hours"][h]["price"] if x["hours"][h]["sales"] is not None and x["hours"][h]["price"] is not None else None) for x in vals]
                hours.append({"hour":h,"sales":sum(points) if all(v is not None for v in points) else None,"estimated_revenue":sum(revenues) if all(v is not None for v in revenues) else None})
            sales.append({"date":d.isoformat(),"sales":sum(x["sales"] for x in vals) if all(x["sales"] is not None for x in vals) else None,"estimated_revenue":sum(x["estimated_revenue"] for x in vals) if all(x["estimated_revenue"] is not None for x in vals) else None,"hours":hours})
        return {"shop":shop,"as_of":datetime.now(TZ).isoformat(),"basis":"店内监控商品快照高水位增量之和","sales":sales}
    finally: conn.close()

@mcp.tool()
def get_product_ranking(metric: str="today") -> dict:
    """Rank active products by today, yesterday, last_hour, cumulative sold or estimated_revenue."""
    if metric not in {"today","yesterday","last_hour","sold","estimated_revenue"}: return {"error":"不支持的排序指标"}
    conn=db.connect()
    try:
        rows=[]
        for p in conn.execute("SELECT * FROM products WHERE active=1"):
            s=product_summary(conn,p["id"]); s.update({"title":p["title"],"shop_name":p["shop_name"]})
            rows.append(s)
        return {"metric":metric,"as_of":datetime.now(TZ).isoformat(),"basis":"快照差值；模糊销售额=销量增量×当时价格，仅为试算","products":sorted(rows,key=lambda x:x.get(metric) if x.get(metric) is not None else -1,reverse=True)}
    finally: conn.close()

@mcp.tool()
def get_shop_ranking(metric: str="today") -> dict:
    """Rank shops by today's, yesterday's, or last hour's snapshot-derived sales."""
    if metric not in {"today","yesterday","last_hour"}: return {"error":"不支持的排序指标"}
    conn=db.connect()
    try: return {"metric":metric,"as_of":datetime.now(TZ).isoformat(),"basis":"监控商品累计已售快照高水位增量之和","shops":shop_ranking(conn,metric)}
    finally: conn.close()

@mcp.tool()
def search_products(query: str) -> dict:
    """Search active and delisted products by title, shop name or product ID."""
    conn=db.connect()
    try:
        rows=[dict(r) for r in conn.execute("SELECT * FROM products WHERE title LIKE ? OR shop_name LIKE ? OR id LIKE ? LIMIT 200",(f"%{query}%",)*3)]
        return {"as_of":datetime.now(TZ).isoformat(),"products":rows}
    finally: conn.close()

@mcp.tool()
def get_overview() -> dict:
    """Get product/shop counts, daily snapshot deltas and latest capture time."""
    conn=db.connect()
    try:
        counts=conn.execute("SELECT COUNT(*) products,COUNT(DISTINCT shop_name) shops FROM products WHERE active=1").fetchone()
        rows=shop_ranking(conn,"today"); yesterday=shop_ranking(conn,"yesterday"); hours=shop_ranking(conn,"last_hour")
        latest=conn.execute("SELECT MAX(captured_at) FROM snapshots").fetchone()[0]
        def total(items,key): return sum(x[key] for x in items) if items and all(x[key] is not None for x in items) else None
        return {"as_of":datetime.now(TZ).isoformat(),"products":counts["products"],"shops":counts["shops"],"today_sales":total(rows,"today"),"yesterday_sales":total(yesterday,"yesterday"),"last_hour_sales":total(hours,"last_hour"),"data_through":latest,"basis":"有效累计已售快照差值；缺少基线时返回 null"}
    finally: conn.close()

if __name__=="__main__":
    # Give the stdio transport duplicate descriptors. Some frozen runtimes close
    # wrapped standard streams during shutdown; keep PyInstaller's originals alive
    # until its own finalization has completed.
    original_stdin,original_stdout=sys.stdin,sys.stdout
    try:
        if original_stdin is not None and original_stdout is not None:
            sys.stdin=io.TextIOWrapper(os.fdopen(os.dup(original_stdin.fileno()),"rb"),encoding="utf-8",errors="replace")
            sys.stdout=io.TextIOWrapper(os.fdopen(os.dup(original_stdout.fileno()),"wb"),encoding="utf-8",line_buffering=True)
            sys.__stdin__,sys.__stdout__=sys.stdin,sys.stdout
        mcp.run(transport="stdio")
    finally:
        sys.stdin,sys.stdout=original_stdin,original_stdout
        sys.__stdin__,sys.__stdout__=original_stdin,original_stdout
