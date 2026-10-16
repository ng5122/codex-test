"""Public product fetch/parsing with conservative, explicit failure classification."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PRODUCT_ID_RE=re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{24}(?![0-9a-f])")
GOODS_PATH_RE=re.compile(r"(?i)/goods-detail/([0-9a-f]{24})")
API="https://mall.xiaohongshu.com/api/store/jpd/edith/detail/h5/toc?version=0.0.5&item_id={}"
DETAIL="https://www.xiaohongshu.com/goods-detail/{}"
DELISTED=("已下架，进店逛逛","当前商品已下架","商品不存在","商品已失效","当前商品违规，无法展示","item freeze")


class CollectError(Exception):
    def __init__(self,message,*,kind="temporary",status=None,details=""):
        super().__init__(message); self.kind=kind; self.status=status; self.details=details


@dataclass
class FetchResult:
    data: dict | None = None
    kind: str = "success"
    reason: str = ""
    method: str = "http"
    details: str = ""


def parse_product_id(text: str) -> str | None:
    match=GOODS_PATH_RE.search(text)
    if match: return match.group(1).lower()
    match=PRODUCT_ID_RE.search(text)
    return match.group(0).lower() if match else None


def parse_count(value) -> int | None:
    if value is None: return None
    if isinstance(value,(int,float)): return max(0,int(value))
    s=str(value).strip().lower().replace(",","").replace("+","")
    s=re.sub(r"^(已售|累计已售|销量)\s*[:：]?\s*", "", s)
    m=re.search(r"(\d+(?:\.\d+)?)\s*(万|w|千|k)?",s)
    if not m: return None
    n=float(m.group(1)); unit=m.group(2)
    return int(n*(10000 if unit=="万" else 1000 if unit in ("千","k") else 10000 if unit=="w" else 1))


def _get(obj,*keys,default=None):
    for key in keys:
        if not isinstance(obj,dict): return default
        obj=obj.get(key)
    return default if obj is None else obj


def parse_api(payload, product_id: str) -> FetchResult:
    if not isinstance(payload,dict): return FetchResult(kind="temporary",reason="响应不是 JSON 对象")
    data=payload.get("data") or {}
    # Explicit delisting is a business result, never inferred from empty/error responses.
    text=json.dumps(payload,ensure_ascii=False).lower()
    if "unbuyablegoshop" in text or "item freeze" in text or any(term.lower() in text for term in DELISTED):
        violation="违规" in text or "freeze" in text
        return FetchResult(kind="delisted",reason="违规下架" if violation else "商品已下架")
    templates=data.get("template_data")
    if payload.get("success") is not True or not isinstance(templates,list) or not templates or not isinstance(templates[0],dict):
        return FetchResult(kind="temporary",reason="接口未返回完整商品数据")
    t=templates[0]
    title=_get(t,"descriptionMain","name") or _get(t,"descriptionH5","name") or product_id
    seller=_get(t,"sellerH5",default={}) or {}
    price_obj=_get(t,"priceH5",default={}) or {}
    bottom=_get(t,"bottomBarMainH5",default={}) or {}
    price=_get(price_obj,"dealPrice","price")
    if price is None: price=_get(bottom,"dealPrice","price")
    if price is None: price=_get(price_obj,"highlightPrice")
    try: price=float(re.sub(r"[^0-9.]","",str(price))) if price is not None else None
    except ValueError: price=None
    sales_text=_get(price_obj,"itemAnalysisDataText")
    sold=parse_count(sales_text)
    if sold is None and sales_text in (None,""):
        sold=0 # Empty sold means zero only after success and a complete template were confirmed above.
    if sold is None:return FetchResult(kind="temporary",reason="累计销量字段格式无法识别")
    images=_get(t,"carouselH5","images",default=[]) or []
    cover=images[0].get("url") if images and isinstance(images[0],dict) else None
    if isinstance(cover,str) and cover.startswith("//"): cover="https:"+cover
    fans=parse_count(_get(t,"profitBarPopupH5","follow","fansNum"))
    if fans is None: fans=parse_count(_get(seller,"fansAmount"))
    return FetchResult(data={"id":product_id,"title":str(title),"shop_name":seller.get("name"),"shop_id":seller.get("id"),"cover":cover,
        "sold":sold,"shop_sold":parse_count(seller.get("salesVolume")),"price":price,"fans":fans,
        "stock_status":_get(t,"carouselH5","stockStatus"),"deliverable":_get(bottom,"deliveryInfo","ableToDelivery")})


class Collector:
    def __init__(self, interval=(0.1,0.3), on_circuit=None, cancel_event=None):
        self.interval=interval; self.on_circuit=on_circuit; self._context=None; self._playwright=None
        self.cancel_event=cancel_event
        self._visible_context=None; self._visible_playwright=None; self._temp_profile=None

    def _http(self,url,timeout=12):
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept":"application/json,text/html,*/*","Referer":"https://www.xiaohongshu.com/"})
        try:
            with urllib.request.urlopen(req,timeout=timeout) as r: return r.status,r.read(5_000_000).decode("utf-8","replace"),r.geturl()
        except urllib.error.HTTPError as e:
            body=e.read(2_000_000).decode("utf-8","replace")
            if e.code==461: raise CollectError("请求环境受限（HTTP 461）",kind="restricted",status=461,details=body[:1000])
            if "unbuyablegoshop" in body.lower() or any(t.lower() in body.lower() for t in DELISTED): return e.code,body,e.geturl()
            raise CollectError(f"HTTP {e.code}",status=e.code,details=body[:1000]) from e
        except (TimeoutError,urllib.error.URLError,OSError) as e: raise CollectError(f"网络请求失败：{e}",details=repr(e)) from e

    @staticmethod
    def _decode(status,body,pid,method):
        if any(t.lower() in body.lower() for t in DELISTED): return FetchResult(kind="delisted",reason="违规下架" if "违规" in body or "freeze" in body.lower() else "商品已下架",method=method)
        if status==461:return FetchResult(kind="restricted",reason="请求环境受限（HTTP 461）",method=method)
        if status < 200 or status >= 300:return FetchResult(kind="temporary",reason=f"HTTP {status}",method=method)
        try: payload=json.loads(body)
        except json.JSONDecodeError: return FetchResult(kind="temporary",reason="响应 JSON 解析失败",method=method)
        result=parse_api(payload,pid); result.method=method
        return result

    def _browser_context(self,headless=True,persistent=True):
        if getattr(__import__("sys"),"frozen",False):
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",str(Path(__import__("sys")._MEIPASS)/".pw-browsers"))
        try: from playwright.sync_api import sync_playwright
        except ImportError as e: raise CollectError("Playwright 未安装",details=str(e))
        if persistent and self._context: return self._context
        if self._playwright is None: self._playwright=sync_playwright().start()
        browser_type=self._playwright.chromium
        chrome_available=bool(shutil.which("chrome"));edge_available=bool(shutil.which("msedge"))
        # Also check standard Windows installs when Chrome/Edge are not on PATH.
        if __import__("os").name=="nt":
            import os
            roots=[Path(os.environ.get("PROGRAMFILES",r"C:\Program Files")),Path(os.environ.get("PROGRAMFILES(X86)",r"C:\Program Files (x86)")),Path(os.environ.get("LOCALAPPDATA",Path.home()/"AppData"/"Local"))]
            chrome_available=chrome_available or any((root/"Google/Chrome/Application/chrome.exe").exists() for root in roots)
            edge_available=edge_available or any((root/"Microsoft/Edge/Application/msedge.exe").exists() for root in roots)
        channels=(["chrome"] if chrome_available else [])+(["msedge"] if edge_available else [])+[None]
        def launch_context(profile,visible):
            last_error=None
            for channel in channels:
                options={"headless":visible is False,"locale":"zh-CN","timezone_id":"Asia/Shanghai","viewport":{"width":1280,"height":900}}
                if channel:options["channel"]=channel
                try:return browser_type.launch_persistent_context(str(profile),**options)
                except Exception as exc:last_error=exc
            raise CollectError("找不到可用浏览器，请安装最新版 Chrome 或 Microsoft Edge；Playwright Chromium 也未能启动。",details=str(last_error))
        if persistent:
            profile=Path.home()/".red-potato-radar"/"browser-profile"; profile.mkdir(parents=True,exist_ok=True)
            self._context=launch_context(profile,not headless)
            return self._context
        self._temp_profile=tempfile.mkdtemp(prefix="rpr-profile-")
        self._visible_playwright=sync_playwright().start()
        browser_type=self._visible_playwright.chromium
        last_error=None
        for channel in channels:
            options={"headless":headless,"locale":"zh-CN","timezone_id":"Asia/Shanghai","viewport":{"width":1280,"height":900}}
            if channel:options["channel"]=channel
            try:self._visible_context=browser_type.launch_persistent_context(self._temp_profile,**options);break
            except Exception as exc:last_error=exc
        if not self._visible_context:
            raise CollectError("找不到可用浏览器，请安装最新版 Chrome 或 Microsoft Edge。",details=str(last_error))
        return self._visible_context

    def _browser_fetch(self,pid,headless):
        context=self._browser_context(headless=headless,persistent=headless)
        page=context.new_page()
        try:
            response=page.request.get(API.format(pid),timeout=15000)
            body=response.text()
            result=self._decode(response.status,body,pid,"headless" if headless else "visible")
            if result.data or result.kind=="delisted": return result
            page.goto(DETAIL.format(pid),wait_until="domcontentloaded",timeout=25000)
            response=page.request.get(API.format(pid),timeout=15000)
            return self._decode(response.status,response.text(),pid,"headless" if headless else "visible")
        finally:
            page.close()

    def fetch(self,pid):
        if self.cancel_event and self.cancel_event.is_set():return FetchResult(kind="temporary",reason="采集轮次已停止")
        try:
            status,body,_=self._http(API.format(pid))
            result=self._decode(status,body,pid,"http")
            if result.data or result.kind=="delisted": return result
        except CollectError as e:
            result=FetchResult(kind=e.kind,reason=str(e),details=e.details)
        # Escalate immediately for this product before moving to the next product.
        for headless in (True,False):
            if self.cancel_event and self.cancel_event.is_set():return FetchResult(kind="temporary",reason="采集轮次已停止")
            time.sleep(random.uniform(max(.1,self.interval[0]),max(self.interval[0],self.interval[1])))
            try:
                result=self._browser_fetch(pid,headless)
                if result.data or result.kind=="delisted": return result
            except Exception as e:
                result=FetchResult(kind="restricted" if getattr(e,"status",None)==461 else "temporary",reason=f"浏览器采集失败：{e}",details=repr(e))
        return result

    def collect(self,pid):
        result=self.fetch(pid)
        if not self.cancel_event or not self.cancel_event.is_set():
            time.sleep(random.uniform(max(0.1,self.interval[0]),max(self.interval[0],self.interval[1])))
        return result

    def open_verification_browser(self):
        context=self._browser_context(headless=False,persistent=True)
        page=context.new_page()
        page.goto("https://www.xiaohongshu.com",wait_until="domcontentloaded",timeout=30000)
        page.wait_for_event("close",timeout=24*60*60*1000)

    def close(self):
        for context in (self._context,self._visible_context):
            if context:
                try: context.close()
                except Exception: pass
        for p in (self._playwright,self._visible_playwright):
            if p:
                try: p.stop()
                except Exception: pass
        if self._temp_profile:
            import shutil as _shutil
            _shutil.rmtree(self._temp_profile,ignore_errors=True)

