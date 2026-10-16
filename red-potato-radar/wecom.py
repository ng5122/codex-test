"""Enterprise WeChat robot notifications."""
from __future__ import annotations
import json
import re
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Shanghai")
WEBHOOK_RE=re.compile(r"^https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]{16,}$")

def valid_webhook(url: str) -> bool:
    return bool(WEBHOOK_RE.fullmatch((url or "").strip()))

def safe_error(error: Exception, url: str="") -> str:
    message=str(error)
    if url:
        message=message.replace(url,"[Webhook 已隐藏]")
        key=url.partition("key=")[2]
        if key:message=message.replace(key,"[已隐藏]")
    return message

def _post(url: str, text: str):
    body=json.dumps({"msgtype":"text","text":{"content":text}},ensure_ascii=False).encode()
    req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=8) as response:
        payload=json.loads(response.read(128_000).decode("utf-8","replace"))
    if payload.get("errcode")!=0: raise RuntimeError(f"企业微信返回错误码 {payload.get('errcode')}")

def send_text(url: str, text: str):
    if not valid_webhook(url): raise ValueError("Webhook 格式不正确")
    # Keep each message comfortably below the robot text payload limit.
    chunks=[]; current=""
    for line in text.splitlines():
        candidate=(current+"\n"+line).strip()
        if len(candidate.encode("utf-8"))>3500 and current:
            chunks.append(current); current=line
        else: current=candidate
    if current: chunks.append(current)
    for chunk in chunks: _post(url,chunk)

def hourly_report(shop_rows, now=None):
    now=now or datetime.now(TZ)
    prev=now.replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)
    label=f"{prev:%m月%d日 %H}:00 - {prev:%H}:59"
    out=[f"📊 {now.hour}点店铺销量时报",label]
    for i,s in enumerate(shop_rows,1):
        badge={1:"🥇",2:"🥈",3:"🥉"}.get(i,f"{i}.")
        out += [f"{badge} {s['shop_name']}",f"上小时销量：{s['last_hour'] if s['last_hour'] is not None else '—'} 单",f"今日总销量：{s['today'] if s['today'] is not None else '—'} 单"]
    return "\n".join(out)
