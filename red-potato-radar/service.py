"""Collection orchestration and boundary-aligned scheduling."""
from __future__ import annotations
import random, threading, time, traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from analytics import shop_ranking
from collector import Collector, parse_product_id
from database import Database
from wecom import hourly_report, safe_error, send_text, valid_webhook

TZ=ZoneInfo("Asia/Shanghai")

class MonitorService:
    def __init__(self,db:Database,on_event=None):
        self.db=db; self.on_event=on_event or (lambda *_:None)
        self.lock=threading.Lock(); self.stop_event=threading.Event(); self.scheduler=None
        self._cancel_round=threading.Event()
        self.interval_minutes=60; self._cooldown_until=0; self._collector=None
        self._cooldown_queue=[]; self._cooldown_timer=None; self._auto_resume=False; self.monitoring=False

    def emit(self,level,message,product_id=None,method="service",details=None):
        self.db.log(level,message,source=method,product_id=product_id,details=details)
        self.on_event(level,message)

    def parse_and_add(self,text,confirm=None):
        if not self.lock.acquire(blocking=False):raise RuntimeError("采集任务正在运行，请稍后添加商品")
        try:
            return self._parse_and_add_locked(text,confirm)
        finally:
            self.lock.release()

    def _parse_and_add_locked(self,text,confirm=None):
        pid=parse_product_id(text)
        if not pid and text.strip().lower().startswith(("http://","https://")):
            try:
                import urllib.request
                req=urllib.request.Request(text.strip(),headers={"User-Agent":"Mozilla/5.0"})
                with urllib.request.urlopen(req,timeout=12) as r: pid=parse_product_id(r.geturl()+" "+r.read(1_000_000).decode("utf-8","ignore"))
            except Exception as e: raise ValueError(f"短链解析失败：{e}")
        if not pid: raise ValueError("未找到有效的 24 位商品 ID")
        if any(p["id"]==pid for p in self.db.active_products()): return "duplicate",pid,None
        try:
            with self.db.connect() as c:settings=dict(c.execute("SELECT key,value FROM settings"))
            interval=(float(settings.get("min_delay","0.1")),float(settings.get("max_delay","0.3")))
        except Exception:interval=(0.1,0.3)
        collector=Collector(interval=interval);self._collector=collector
        try:result=collector.collect(pid)
        finally:collector.close();self._collector=None
        if result.kind=="delisted": return "delisted",pid,result.reason
        if not result.data: return "pending",pid,result.reason
        item=result.data
        if item["sold"]>10000: return "over_limit",pid,item
        if confirm and not confirm(item): return "cancelled",pid,item
        self.db.upsert_product(item)
        self.db.save_snapshot(pid,datetime.now(TZ).replace(microsecond=0).isoformat(),item)
        return "added",pid,item

    def collect_round(self,scheduled_at=None,product_ids=None):
        if time.time()<self._cooldown_until: return {"skipped":"HTTP 461 冷却中"}
        if not self.lock.acquire(blocking=False): return {"skipped":"已有采集轮次运行"}
        self._cancel_round.clear()
        scheduled_at=scheduled_at or self._floor_boundary(datetime.now(TZ))
        timestamp=scheduled_at.isoformat(timespec="seconds")
        products=[p for p in self.db.active_products() if product_ids is None or p["id"] in product_ids]
        try:
            with self.db.connect() as c: settings=dict(c.execute("SELECT key,value FROM settings"))
            interval=(float(settings.get("min_delay","0.1")),float(settings.get("max_delay","0.3")))
        except Exception: interval=(0.1,0.3)
        collector=Collector(interval=interval,cancel_event=self._cancel_round); self._collector=collector
        round_deadline=scheduled_at+timedelta(minutes=self.interval_minutes)
        expiry=threading.Timer(max(0,(round_deadline-datetime.now(TZ)).total_seconds()),self._expire_round,args=(collector,))
        expiry.daemon=True;expiry.start()
        failures=[]; restricted=0; succeeded=0; halted=False
        try:
            for index,product in enumerate(products):
                if self._cancel_round.is_set(): break
                if self._floor_boundary(datetime.now(TZ))>scheduled_at:
                    self.emit("info","旧轮次已跨入下一整点，停止剩余商品"); halted=True; break
                try:
                    result=collector.collect(product["id"])
                    if self._cancel_round.is_set():halted=True;break
                    if result.kind=="success" and result.data:
                        restricted=0
                        if result.data["sold"]>10000:
                            self.db.deactivate(product["id"]); self.emit("info","累计销量超过10000，已停止监控",product["id"]); continue
                        self.db.save_snapshot(product["id"],timestamp,result.data); succeeded+=1
                    elif result.kind=="delisted": restricted=0; self.db.delist(product["id"],result.reason); self.emit("info",result.reason,product["id"])
                    else:
                        if result.kind=="restricted":
                            restricted+=1
                            if restricted>=3:
                                failures.append((product,result)); self.db.record_error(product["id"],result.reason,result.reason)
                                self._cooldown_until=time.time()+900
                                pending=[p["id"] for p,_ in failures]+[p["id"] for p in products[index+1:]]
                                self._cooldown_queue=list(dict.fromkeys(pending)); halted=True
                                self._auto_resume=True;self._arm_cooldown_resume()
                                self.emit("error","HTTP 461 达到熔断阈值，暂停采集15分钟"); break
                        else: restricted=0
                        failures.append((product,result)); self.db.record_error(product["id"],result.reason,result.details or result.reason)
                except Exception as e:
                    restricted=0; failures.append((product,None)); self.db.record_error(product["id"],str(e),traceback.format_exc())
            if failures and not halted and self._floor_boundary(datetime.now(TZ))<=scheduled_at:
                self._cancel_round.wait(random.uniform(5,15)); remaining=[];retry_restricted=0
                for retry_index,(product,_) in enumerate(failures):
                    if self._cancel_round.is_set(): break
                    if self._floor_boundary(datetime.now(TZ))>scheduled_at:halted=True;break
                    try:
                        result=collector.collect(product["id"])
                        if result.data:
                            retry_restricted=0
                            if result.data["sold"]>10000: self.db.deactivate(product["id"])
                            else:self.db.save_snapshot(product["id"],timestamp,result.data); succeeded+=1
                        elif result.kind=="delisted":retry_restricted=0;self.db.delist(product["id"],result.reason)
                        else:
                            remaining.append((product,result));self.db.record_error(product["id"],result.reason,result.details or result.reason)
                            if result.kind=="restricted":retry_restricted+=1
                            else:retry_restricted=0
                            if retry_restricted>=3:
                                self._cooldown_until=time.time()+900
                                self._cooldown_queue=list(dict.fromkeys(p["id"] for p,_ in remaining+failures[retry_index+1:]))
                                self._auto_resume=True;self._arm_cooldown_resume();halted=True
                                self.emit("error","重试期间 HTTP 461 达到熔断阈值，暂停采集15分钟");break
                    except Exception as e:retry_restricted=0;self.db.record_error(product["id"],str(e),traceback.format_exc());remaining.append((product,None))
                failures=remaining
            self._send_reports(timestamp,failures)
            self.emit("success",f"采集轮次完成：成功 {succeeded}，失败 {len(failures)}，下架/超限已处理")
            return {"success":succeeded,"failures":len(failures),"halted":halted}
        finally:
            expiry.cancel();collector.close(); self._collector=None; self.lock.release()

    def _expire_round(self,collector):
        self._cancel_round.set()
        try:collector.close()
        except Exception:pass

    def _send_reports(self,timestamp,failures):
        with self.db.connect() as c: settings=dict(c.execute("SELECT key,value FROM settings"))
        url=settings.get("wecom_webhook","")
        if settings.get("wecom_enabled")!="1" or not valid_webhook(url): return
        chosen=set(filter(None,settings.get("wecom_shops","").splitlines()))
        conn=self.db.connect()
        try: rows=[x for x in shop_ranking(conn,"last_hour") if x["shop_name"] in chosen]
        finally: conn.close()
        try:
            if rows: send_text(url,hourly_report(rows))
            if failures:
                lines=[f"采集失败 {timestamp}，共 {len(failures)} 个商品"]
                lines += [f"{p['title']} ({p['id']})：{r.reason if r else p.get('last_error') or '采集失败'}" for p,r in failures]
                send_text(url,"\n".join(lines))
        except Exception as e:self.emit("error",f"企业微信通知失败：{safe_error(e,url)}")

    def _next_boundary(self,now):
        seconds=self.interval_minutes*60; midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
        elapsed=(now-midnight).total_seconds()
        return midnight+timedelta(seconds=(int(elapsed//seconds)+1)*seconds)

    def _floor_boundary(self,now):
        seconds=self.interval_minutes*60; midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
        elapsed=(now-midnight).total_seconds()
        return midnight+timedelta(seconds=int(elapsed//seconds)*seconds)

    def start(self,minutes=60):
        self.interval_minutes=max(1,int(minutes)); self.stop_event.clear(); self._cancel_round.clear(); self.monitoring=True
        if self.scheduler and self.scheduler.is_alive(): return
        self.scheduler=threading.Thread(target=self._schedule,daemon=True,name="radar-scheduler"); self.scheduler.start()

    def _schedule(self):
        boundary=self._next_boundary(datetime.now(TZ))
        while not self.stop_event.is_set():
            while not self.stop_event.is_set():
                if self.stop_event.wait(min(max(0,(boundary-datetime.now(TZ)).total_seconds()),1)): return
                if datetime.now(TZ)>=boundary: break
            if self.stop_event.is_set(): break
            self.collect_round(boundary)
            boundary=boundary+timedelta(minutes=self.interval_minutes)
            # Do not skip an elapsed boundary: collection checks the hour and stops stale work.
            while boundary+timedelta(minutes=self.interval_minutes)<=datetime.now(TZ):
                boundary+=timedelta(minutes=self.interval_minutes)

    def stop(self):
        self.stop_event.set(); self.monitoring=False;self._auto_resume=False
        if self._cooldown_timer:self._cooldown_timer.cancel()
        self._cancel_round.set()
        if self._collector:
            try:self._collector.close()
            except Exception:pass

    def _resume_cooldown(self):
        if not self._auto_resume or not self._cooldown_queue:return
        if time.time()<self._cooldown_until:
            self._arm_cooldown_resume();return
        ids=set(self._cooldown_queue); self._cooldown_queue=[]; self._cooldown_until=0
        self._auto_resume=False
        threading.Thread(target=self._collect_cooldown_queue,args=(ids,),daemon=True,name="radar-cooldown-resume").start()

    def _collect_cooldown_queue(self,ids):
        while self.lock.locked() and not self.stop_event.wait(0.2):pass
        if self.stop_event.is_set() and not self.monitoring:return
        self.collect_round(product_ids=ids)

    def _arm_cooldown_resume(self):
        if self._cooldown_timer:self._cooldown_timer.cancel()
        self._cooldown_timer=threading.Timer(max(0,self._cooldown_until-time.time()),self._resume_cooldown)
        self._cooldown_timer.daemon=True;self._cooldown_timer.start()
