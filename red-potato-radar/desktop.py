"""Tkinter desktop interface for Red Potato Radar."""
from __future__ import annotations
import csv, json, queue, threading, tkinter as tk, webbrowser
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from urllib.request import Request, urlopen
from analytics import daily_sales, product_summary, shop_ranking
from collector import parse_product_id
from database import Database, TZ
from service import MonitorService
from wecom import safe_error, send_text, valid_webhook

GREEN="#27845a"; BG="#f4f6f5"; TEXT="#26352d"

class RadarApp:
    def __init__(self,root):
        self.root=root; self.db=Database(); self.events=queue.Queue(); self.service=MonitorService(self.db,self._event);self._verification_collectors=[]
        self.root.title("红薯雷达"); self.root.geometry("1280x780"); self.root.minsize(1050,650); self.root.configure(bg=BG)
        icon=Path(__file__).with_name("assets")/"app-icon.ico"
        if icon.exists():
            try:self.root.iconbitmap(str(icon))
            except tk.TclError:pass
        self.style=ttk.Style(); self.style.theme_use("clam"); self.style.configure("TNotebook",background=BG); self.style.configure("Treeview",rowheight=31,font=("Microsoft YaHei UI",10)); self.style.configure("Treeview.Heading",font=("Microsoft YaHei UI",10,"bold")); self.style.map("Treeview",background=[("selected","#d9efe3")],foreground=[("selected",TEXT)])
        self._build(); self.refresh_all(); self._tick(); self.root.protocol("WM_DELETE_WINDOW",self.close)

    def _event(self,level,message): self.events.put((level,message))
    def _build(self):
        header=ttk.Frame(self.root,padding=(12,8)); header.pack(fill="x")
        ttk.Label(header,text="红薯雷达",font=("Microsoft YaHei UI",18,"bold"),foreground=GREEN).pack(side="left")
        ttk.Label(header,text="仅供学习交流，严禁用于商业用途，请于24小时内删除",foreground="#68746d").pack(side="left",padx=16)
        self.status=ttk.Label(header,text="就绪"); self.status.pack(side="right",padx=6)
        self.data_label=ttk.Label(header,text="数据占用：—"); self.data_label.pack(side="right",padx=10)
        ttk.Button(header,text="by：冬青",command=lambda:webbrowser.open("https://scys.com/personal/3941891?number=201255&tab=posts")).pack(side="right",padx=6)
        self.notebook=ttk.Notebook(self.root); self.notebook.pack(fill="both",expand=True,padx=10,pady=(0,10))
        self.tabs={}
        for name in ("竞品看板","添加商品","失败列表","下架列表","店铺分析","设置","运行日志"):
            frame=ttk.Frame(self.notebook); self.notebook.add(frame,text=name); self.tabs[name]=frame
        self._board(); self._add_page(); self._failure_page(); self._delisted_page(); self._shops_page(); self._settings_page(); self._logs_page()

    def _tree(self,parent,columns,headings,heights=14):
        wrap=ttk.Frame(parent); wrap.pack(fill="both",expand=True)
        tree=ttk.Treeview(wrap,columns=columns,show="headings",height=heights,selectmode="extended")
        for col,heading in zip(columns,headings):
            tree.heading(col,text=heading); tree.column(col,width=130,anchor="w",stretch=True)
        y=ttk.Scrollbar(wrap,orient="vertical",command=tree.yview); x=ttk.Scrollbar(wrap,orient="horizontal",command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set); tree.grid(row=0,column=0,sticky="nsew"); y.grid(row=0,column=1,sticky="ns"); x.grid(row=1,column=0,sticky="ew")
        wrap.rowconfigure(0,weight=1); wrap.columnconfigure(0,weight=1); return tree

    def _board(self):
        f=self.tabs["竞品看板"]; top=ttk.Frame(f,padding=10); top.pack(fill="x")
        ttk.Label(top,text="竞品监控看板",font=("Microsoft YaHei UI",14,"bold")).pack(side="left")
        self.search=tk.StringVar(); ent=ttk.Entry(top,textvariable=self.search,width=22); ent.pack(side="left",padx=8); ent.insert(0,""); self.search.trace_add("write",lambda *_:self.refresh_board())
        for label,cmd in (("开始监控",self.start_monitor), ("停止监控",self.stop_monitor), ("立即采集",lambda:self.collect_async()), ("智能去重",self.dedupe), ("自动拓品",self.auto_expand), ("添加商品",lambda:self.notebook.select(self.tabs["添加商品"]))):
            ttk.Button(top,text=label,command=cmd).pack(side="right",padx=3)
        cols=("title","shop_name","price","today","yesterday","last_hour","sold","captured","state")
        names=("商品名称","店铺","当前价格","今日销量","昨日销量","上小时销量","累计已售","最近采集","状态")
        self.products=self._tree(f,cols,names)
        for col in ("today","yesterday","last_hour","sold"):
            self.products.heading(col,text=dict(zip(cols,names))[col]+" ▼",command=lambda c=col:self.sort_board(c))
        self.products.bind("<Double-1>",lambda e:self.show_detail())
        menu=tk.Menu(self.root,tearoff=False); menu.add_command(label="查看详情",command=self.show_detail); menu.add_command(label="立即采集选中商品",command=self.collect_selected); menu.add_command(label="删除商品",command=self.delete_selected)
        def show_context(event):
            row=self.products.identify_row(event.y)
            if row and row not in self.products.selection():self.products.selection_set(row)
            menu.tk_popup(event.x_root,event.y_root)
        self.products.bind("<Button-3>",show_context)
        self._sort=("today",True)

    def _add_page(self):
        f=self.tabs["添加商品"]; ttk.Label(f,text="添加商品",font=("Microsoft YaHei UI",14,"bold")).pack(anchor="w",padx=14,pady=12)
        ttk.Label(f,text="支持分享口令、xhslink 短链、商品链接或24位商品 ID。可一行一个批量导入。",wraplength=850).pack(anchor="w",padx=14)
        self.input=tk.Text(f,height=12,wrap="word"); self.input.pack(fill="x",padx=14,pady=10)
        row=ttk.Frame(f); row.pack(anchor="w",padx=14)
        ttk.Button(row,text="添加商品",command=self.add_one).pack(side="left",padx=(0,8)); ttk.Button(row,text="批量导入",command=self.add_batch).pack(side="left")
        ttk.Label(f,text="若公开接口或页面无法读取完整数据，商品会显示为待采集/失败，不会用0伪造销量。累计已售超过10000的商品不纳入监控。",foreground="#68746d",wraplength=900).pack(anchor="w",padx=14,pady=16)

    def _failure_page(self):
        f=self.tabs["失败列表"]; bar=ttk.Frame(f,padding=8); bar.pack(fill="x")
        for label,cmd in (("全选",lambda:self.failures.selection_set(self.failures.get_children())),("重试选中",lambda:self.collect_ids(self._selected_ids(self.failures))),("忽略选中并保留监控",self.ignore_selected),("删除选中",lambda:self.delete_tree(self.failures)),("刷新",self.refresh_failures)):
            ttk.Button(bar,text=label,command=cmd).pack(side="left",padx=3)
        self.failures=self._tree(f,("title","shop","id","time","reason"),("商品名称","店铺","商品 ID","最近失败时间","失败原因"))
        self.failures.bind("<Double-1>",lambda e:self.show_detail(self.failures))

    def _delisted_page(self):
        f=self.tabs["下架列表"]; ttk.Label(f,text="已明确识别为下架或违规下架的商品仍保留历史记录。",padding=8).pack(anchor="w")
        self.delisted=self._tree(f,("title","shop","id","time","reason"),("商品名称","店铺","商品 ID","下架时间","下架状态"))
        self.delisted.bind("<Double-1>",lambda e:self.show_detail(self.delisted))

    def _shops_page(self):
        f=self.tabs["店铺分析"]; bar=ttk.Frame(f,padding=8); bar.pack(fill="x")
        ttk.Label(bar,text="店铺").pack(side="left"); self.shop_var=tk.StringVar(); self.shop_box=ttk.Combobox(bar,textvariable=self.shop_var,state="readonly",width=28); self.shop_box.pack(side="left",padx=8); self.shop_box.bind("<<ComboboxSelected>>",lambda e:self.refresh_shop())
        self.shop_range=tk.StringVar(value="今日"); cb=ttk.Combobox(bar,textvariable=self.shop_range,values=("今日","近7天","近10天"),state="readonly",width=12); cb.pack(side="left"); cb.bind("<<ComboboxSelected>>",lambda e:self.refresh_shop())
        self.shop_date=tk.StringVar(value=datetime.now(TZ).date().isoformat()); ttk.Entry(bar,textvariable=self.shop_date,width=12).pack(side="left",padx=(8,2)); ttk.Button(bar,text="查看日期",command=self.refresh_shop_date).pack(side="left")
        self.shop_info=ttk.Label(f,text="选择店铺查看累计快照差值",padding=6); self.shop_info.pack(anchor="w")
        self.chart=tk.Canvas(f,height=200,bg="white",highlightthickness=1,highlightbackground="#d7dfda"); self.chart.pack(fill="x",padx=8,pady=4)
        lower=ttk.Panedwindow(f,orient="horizontal"); lower.pack(fill="both",expand=True,padx=8,pady=8)
        left=ttk.Frame(lower); right=ttk.Frame(lower); lower.add(left,weight=1); lower.add(right,weight=1)
        ttk.Label(left,text="销量明细（模糊试算，不代表真实成交额）").pack(anchor="w"); self.shop_sales=self._tree(left,("time","sales","revenue"),("时间","销量增量","模糊销售额"),9)
        ttk.Label(right,text="店铺内监控商品").pack(anchor="w"); self.shop_products=self._tree(right,("title","today","price","sold","id"),("商品名称","今日销量","当前价格","累计已售","商品 ID"),9)
        self.shop_products.bind("<Double-1>",lambda e:self.show_detail(self.shop_products))

    def _settings_page(self):
        f=self.tabs["设置"]; canvas=tk.Canvas(f,highlightthickness=0,bg=BG); scroll=ttk.Scrollbar(f,orient="vertical",command=canvas.yview); content=ttk.Frame(canvas,padding=12)
        content.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all"))); canvas.create_window((0,0),window=content,anchor="nw"); canvas.configure(yscrollcommand=scroll.set); canvas.pack(side="left",fill="both",expand=True); scroll.pack(side="right",fill="y")
        ttk.Label(content,text="监控设置",font=("Microsoft YaHei UI",14,"bold")).grid(row=0,column=0,sticky="w",pady=6)
        self.period=tk.StringVar(value=self.setting("interval_minutes","60")); self.min_delay=tk.StringVar(value=self.setting("min_delay","0.1")); self.max_delay=tk.StringVar(value=self.setting("max_delay","0.3"))
        for i,(label,var) in enumerate((("采集周期（分钟）",self.period),("商品间隔最小秒数",self.min_delay),("商品间隔最大秒数",self.max_delay)),1):
            ttk.Label(content,text=label).grid(row=i,column=0,sticky="w",pady=3); ttk.Entry(content,textvariable=var,width=16).grid(row=i,column=1,sticky="w",padx=8)
        ttk.Button(content,text="保存监控设置",command=self.save_settings).grid(row=4,column=0,sticky="w",pady=6)
        ttk.Button(content,text="打开浏览器处理验证",command=self.open_verification).grid(row=4,column=1,sticky="w")
        self.monitor_info=ttk.Label(content,text="监控商品：0 · 单轮间隔等待约 0 秒",foreground="#68746d");self.monitor_info.grid(row=4,column=2,columnspan=2,sticky="w",padx=8)
        ttk.Separator(content).grid(row=5,column=0,columnspan=4,sticky="ew",pady=10)
        ttk.Label(content,text="企业微信机器人通知",font=("Microsoft YaHei UI",14,"bold")).grid(row=6,column=0,sticky="w",pady=4)
        self.webhook=tk.StringVar(value=self.setting("wecom_webhook","")); self.wecom_enabled=tk.BooleanVar(value=self.setting("wecom_enabled","0")=="1")
        ttk.Label(content,text="Webhook").grid(row=7,column=0,sticky="w"); ttk.Entry(content,textvariable=self.webhook,width=75,show="•").grid(row=7,column=1,columnspan=2,sticky="w")
        ttk.Checkbutton(content,text="启用通知",variable=self.wecom_enabled).grid(row=8,column=1,sticky="w",pady=5)
        ttk.Button(content,text="保存通知设置",command=self.save_settings).grid(row=9,column=1,sticky="w"); ttk.Button(content,text="发送测试通知",command=self.test_wecom).grid(row=9,column=2,sticky="w",padx=8); ttk.Button(content,text="设置通知店铺",command=self.choose_shops).grid(row=9,column=3,sticky="w")
        ttk.Separator(content).grid(row=10,column=0,columnspan=4,sticky="ew",pady=10)
        ttk.Label(content,text="AI / MCP 数据读取",font=("Microsoft YaHei UI",14,"bold")).grid(row=11,column=0,sticky="w",pady=4)
        ttk.Label(content,text="MCP 服务只读，不提供修改数据库的工具。",foreground="#68746d").grid(row=12,column=0,columnspan=3,sticky="w")
        ttk.Button(content,text="复制 MCP 配置说明",command=self.copy_mcp).grid(row=13,column=0,sticky="w",pady=5)
        ttk.Separator(content).grid(row=14,column=0,columnspan=4,sticky="ew",pady=10)
        ttk.Label(content,text="本地数据维护",font=("Microsoft YaHei UI",14,"bold")).grid(row=15,column=0,sticky="w",pady=4)
        ttk.Button(content,text="一键清理（快照90天、日志30天）",command=self.cleanup).grid(row=16,column=0,sticky="w")

    def _logs_page(self):
        f=self.tabs["运行日志"]; bar=ttk.Frame(f,padding=8); bar.pack(fill="x")
        self.log_filter=tk.StringVar(value="全部"); cb=ttk.Combobox(bar,textvariable=self.log_filter,values=("全部","成功","失败","信息"),state="readonly",width=10); cb.pack(side="left"); cb.bind("<<ComboboxSelected>>",lambda e:self.refresh_logs())
        ttk.Button(bar,text="刷新",command=self.refresh_logs).pack(side="left",padx=4); ttk.Button(bar,text="清空日志",command=self.clear_logs).pack(side="left",padx=4); ttk.Button(bar,text="导出 CSV",command=self.export_logs).pack(side="left",padx=4)
        self.logs=self._tree(f,("time","level","source","title","product_id","message"),("时间","级别","来源","商品","商品 ID","摘要"),18)
        self.logs.bind("<Double-1>",self.log_detail)

    def setting(self,key,default=""):
        with self.db.connect() as c:
            row=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
            return row[0] if row else default
    def set_setting(self,key,value):
        with self.db.connect() as c: c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))

    def refresh_all(self): self.refresh_board(); self.refresh_failures(); self.refresh_delisted(); self.refresh_shops(); self.refresh_logs(); self._update_data_label()
    def refresh_board(self):
        if not hasattr(self,"products"): return
        query=self.search.get().casefold() if hasattr(self,"search") else ""
        for iid in self.products.get_children(): self.products.delete(iid)
        with self.db.connect() as c: rows=c.execute("SELECT * FROM products WHERE active=1 ORDER BY title").fetchall()
        items=[]
        with self.db.connect() as c:
            for p in rows:
                s=product_summary(c,p["id"]); match=" ".join([p["title"] or "",p["shop_name"] or "",p["id"]]).casefold()
                if query and query not in match: continue
                state="异常" if p["last_error"] else "正常" if s["sold"] is not None else "待采集"
                items.append((p,s,state))
        col,desc=self._sort
        items.sort(key=lambda x:x[1].get(col) if x[1].get(col) is not None else 0,reverse=desc)
        items.sort(key=lambda x:x[1].get(col) is None)
        for p,s,state in items: self.products.insert("","end",iid=p["id"],values=(p["title"],p["shop_name"] or "—",s["price"] if s["price"] is not None else "—",s["today"] if s["today"] is not None else "—",s["yesterday"] if s["yesterday"] is not None else "—",s["last_hour"] if s["last_hour"] is not None else "—",s["sold"] if s["sold"] is not None else "—",s["last_captured_at"] or "—",state))
    def sort_board(self,col):
        self._sort=(col,not self._sort[1] if self._sort[0]==col else True); self.refresh_board()
    def refresh_failures(self):
        if not hasattr(self,"failures"): return
        for i in self.failures.get_children(): self.failures.delete(i)
        with self.db.connect() as c:
            for p in c.execute("""SELECT p.*,(SELECT MAX(l.time) FROM logs l WHERE l.product_id=p.id AND l.level='error') AS failure_time
                FROM products p WHERE p.active=1 AND p.last_error IS NOT NULL ORDER BY failure_time DESC"""):
                self.failures.insert("","end",iid=p["id"],values=(p["title"],p["shop_name"] or "—",p["id"],p["failure_time"] or "—",p["last_error"]))
    def refresh_delisted(self):
        if not hasattr(self,"delisted"): return
        for i in self.delisted.get_children(): self.delisted.delete(i)
        with self.db.connect() as c:
            for p in c.execute("SELECT * FROM products WHERE active=0 AND delisted_at IS NOT NULL ORDER BY delisted_at DESC"):
                self.delisted.insert("","end",iid=p["id"],values=(p["title"],p["shop_name"] or "—",p["id"],p["delisted_at"],p["delisted_reason"]))
    def refresh_shops(self):
        if not hasattr(self,"shop_box"): return
        with self.db.connect() as c: shops=[r[0] for r in c.execute("SELECT DISTINCT shop_name FROM products WHERE active=1 AND shop_name IS NOT NULL ORDER BY shop_name")]
        self.shop_box["values"]=shops
        if self.shop_var.get() not in shops: self.shop_var.set(shops[0] if shops else "")
        self.refresh_shop()
    def refresh_shop(self):
        if not hasattr(self,"shop_sales"): return
        for tree in (self.shop_sales,self.shop_products):
            for i in tree.get_children(): tree.delete(i)
        shop=self.shop_var.get()
        if not shop: return
        with self.db.connect() as c:
            products=[dict(p) for p in c.execute("SELECT * FROM products WHERE active=1 AND shop_name=?",(shop,))]
            infos=[(p,product_summary(c,p["id"])) for p in products]
            period=self.shop_range.get()
            if period=="指定日期":
                selected=date.fromisoformat(self.shop_date.get().strip()); day_list=[selected]; days=1
            else:
                days=1 if period=="今日" else 7 if period=="近7天" else 10
                day_list=[datetime.now(TZ).date()-timedelta(days=offset) for offset in range(days-1,-1,-1)]
            points=[]
            for d in day_list:
                vals=[daily_sales(c,p["id"],d) for p in products]
                sales=sum(v["sales"] for v in vals) if vals and all(v["sales"] is not None for v in vals) else None
                points.append((d,sales))
                if days==1:
                    for h in range(24):
                        xs=[v["hours"][h]["sales"] for v in vals]
                        value=sum(xs) if xs and all(x is not None for x in xs) else None
                        revenue=sum(a[h]["sales"]*a[h]["price"] for a in hours) if value is not None and all(a[h]["sales"] is not None and a[h]["price"] is not None for a in hours) else None
                        self.shop_sales.insert("","end",values=(f"{h:02d}:00",value if value is not None else "—",f"≈{revenue:.2f}" if revenue is not None else "—"))
                else:
                    revenue=sum(v["estimated_revenue"] for v in vals) if sales is not None and all(v["estimated_revenue"] is not None for v in vals) else None
                    self.shop_sales.insert("","end",values=(d.isoformat(),sales if sales is not None else "—",f"≈{revenue:.2f}" if revenue is not None else "—"))
            for p,s in sorted(infos,key=lambda x:x[1]["today"] if x[1]["today"] is not None else -1,reverse=True):
                self.shop_products.insert("","end",iid=p["id"],values=(p["title"],s["today"] if s["today"] is not None else "—",s["price"] if s["price"] is not None else "—",s["sold"] if s["sold"] is not None else "—",p["id"]))
            self.shop_info.config(text=f"{shop}  ·  监控商品 {len(products)} 个  ·  累计已售 {sum(s['sold'] or 0 for _,s in infos)}  ·  {period}")
            if period in ("今日","指定日期"):
                selected_day=date.fromisoformat(self.shop_date.get().strip()) if period=="指定日期" else datetime.now(TZ).date()
                hours=[daily_sales(c,p["id"],selected_day)["hours"] for p in products]
                chart_points=[(f"{h:02d}:00",sum(a[h]["sales"] for a in hours) if hours and all(a[h]["sales"] is not None for a in hours) else None) for h in range(24)]
            else:chart_points=points
            self._draw_chart(chart_points)
    def refresh_shop_date(self):
        try:date.fromisoformat(self.shop_date.get().strip());self.shop_range.set("指定日期");self.refresh_shop()
        except ValueError:messagebox.showerror("日期格式","请输入 YYYY-MM-DD 格式的日期。",parent=self.root)
    def _draw_chart(self,points):
        c=self.chart; c.delete("all"); w=max(1,c.winfo_width()); h=max(1,c.winfo_height()); vals=[v for _,v in points if v is not None]
        if not vals: c.create_text(w/2,h/2,text="暂无足够快照计算销量",fill="#718078"); return
        lo=min(vals); hi=max(vals); span=max(1,hi-lo); coords=[]
        for i,(_,v) in enumerate(points):
            if v is None: continue
            x=35+(w-60)*(i/max(1,len(points)-1)); y=h-30-(h-55)*(v-lo)/span; coords.extend((x,y))
        if len(coords)>=4: c.create_line(*coords,fill=GREEN,width=2,smooth=True)
        for x,y in zip(coords[0::2],coords[1::2]): c.create_oval(x-3,y-3,x+3,y+3,fill=GREEN,outline="")
        c.create_text(36,12,anchor="w",text=f"销量增量  最低 {lo} / 最高 {hi}",fill=TEXT)

    def refresh_logs(self):
        if not hasattr(self,"logs"): return
        for i in self.logs.get_children(): self.logs.delete(i)
        filt=self.log_filter.get() if hasattr(self,"log_filter") else "全部"; map_level={"成功":"success","失败":"error","信息":"info"}
        with self.db.connect() as c:
            query="SELECT * FROM logs"; args=()
            if filt in map_level: query+=" WHERE level=?"; args=(map_level[filt],)
            query+=" ORDER BY id DESC LIMIT 1000"
            for r in c.execute(query,args): self.logs.insert("","end",iid=str(r["id"]),values=(r["time"],r["level"],r["source"],r["title"] or "—",r["product_id"] or "—",r["message"]))
    def _update_data_label(self):
        try:
            size=sum(Path(str(self.db.path)+suffix).stat().st_size for suffix in ("","-wal","-shm") if Path(str(self.db.path)+suffix).exists())
            self.data_label.config(text=f"数据占用：{size/1024/1024:.1f}MB · {self.db.snapshot_count():,}条")
        except OSError:pass
        if hasattr(self,"monitor_info"):
            count=len(self.db.active_products())
            try:delay=max(0.1,float(self.max_delay.get()))
            except (ValueError,AttributeError):delay=0.3
            self.monitor_info.config(text=f"监控商品：{count} · 单轮间隔等待约 {count*delay:.1f} 秒")
    def _tick(self):
        dirty=False
        try:
            while True:
                level,msg=self.events.get_nowait(); self.status.config(text=msg[:65]); dirty=True
        except queue.Empty: pass
        if dirty:self.refresh_all()
        if self.service.monitoring and self.service.scheduler and self.service.scheduler.is_alive():
            boundary=self.service._next_boundary(datetime.now(TZ));self.status.config(text=f"监控中 · 下次采集 {boundary:%m-%d %H:%M}")
        self.root.after(600,self._tick)

    def add_one(self):
        text=self.input.get("1.0","end").strip()
        if not text: messagebox.showinfo("添加商品","请先输入商品口令、链接或 ID。",parent=self.root); return
        self._add_text(text,ask=True)
    def _add_text(self,text,ask=False):
        def work():
            try:
                def confirm(item):
                    if not ask:return True
                    answer=[]; done=threading.Event()
                    def prompt(): answer.append(messagebox.askyesno("确认添加",f"商品：{item['title']}\n店铺：{item.get('shop_name') or '—'}\n价格：{item.get('price') if item.get('price') is not None else '—'}\n累计已售：{item['sold']}\n\n加入监控？",parent=self.root)); done.set()
                    self.root.after(0,prompt); done.wait(); return bool(answer and answer[0])
                result,pid,info=self.service.parse_and_add(text,confirm)
                labels={"added":"已添加","duplicate":"重复，已跳过","pending":"待采集，未保存假快照","delisted":"商品已下架","over_limit":"超过销量上限，已跳过","cancelled":"已取消"}
                self._event("info",f"{pid}：{labels[result]}")
                if result in ("pending","delisted"):
                    self.db.upsert_product({"id":pid,"title":pid});
                    if result=="delisted":self.db.delist(pid,str(info))
                    else:self.db.record_error(pid,str(info or "首次采集失败"))
            except Exception as e:self._event("error",str(e))
        threading.Thread(target=work,daemon=True).start()
    def add_batch(self):
        lines=[x.strip() for x in self.input.get("1.0","end").splitlines() if x.strip()]
        def work():
            counts={k:0 for k in ("成功","待采集","下架","重复","超过销量上限","无效输入")}; seen=set()
            for line in lines:
                pid=parse_product_id(line)
                if pid and pid in seen: counts["重复"]+=1; continue
                if pid: seen.add(pid)
                try:
                    result,pid,_=self.service.parse_and_add(line)
                    key={"added":"成功","pending":"待采集","delisted":"下架","duplicate":"重复","over_limit":"超过销量上限","cancelled":"待采集"}[result]
                    counts[key]+=1
                    if result in ("pending","delisted"):
                        self.db.upsert_product({"id":pid,"title":pid})
                        if result=="delisted": self.db.delist(pid,"商品已下架")
                        else:self.db.record_error(pid,"首次采集失败")
                except Exception:self.db.log("error","无效输入或短链解析失败",source="batch"); counts["无效输入"]+=1
            self.root.after(0,lambda:messagebox.showinfo("批量导入","\n".join(f"{k}：{v}" for k,v in counts.items()),parent=self.root)); self._event("info","批量导入处理完成")
        threading.Thread(target=work,daemon=True).start()

    def start_monitor(self):
        try:self.service.start(int(self.period.get())); self.set_setting("interval_minutes",self.period.get()); self.status.config(text=f"监控已启动，下次整点采集")
        except Exception as e:messagebox.showerror("设置错误",str(e),parent=self.root)
    def stop_monitor(self): self.service.stop(); self.status.config(text="监控已停止")
    def open_verification(self):
        def work():
            from collector import Collector
            collector=Collector()
            self._verification_collectors.append(collector)
            try:collector.open_verification_browser();self.db.log("info","浏览器验证窗口已关闭，登录状态已保存在本机配置",source="browser");self._event("info","浏览器验证窗口已关闭，登录状态已保存到本机配置")
            except Exception as e:self.db.log("error",f"无法打开验证浏览器：{e}",source="browser");self._event("error",f"无法打开验证浏览器：{e}")
            finally:
                collector.close()
                if collector in self._verification_collectors:self._verification_collectors.remove(collector)
        threading.Thread(target=work,daemon=True,name="verification-browser").start()
    def collect_async(self):
        if not self.service.lock.locked(): threading.Thread(target=self.service.collect_round,daemon=True).start()
        else: messagebox.showinfo("采集状态","已有采集任务正在运行。",parent=self.root)
    def collect_ids(self,ids):
        if ids:threading.Thread(target=self.service.collect_round,kwargs={"product_ids":set(ids)},daemon=True).start()
    def collect_selected(self):self.collect_ids(self._selected_ids(self.products))
    @staticmethod
    def _selected_ids(tree):return [str(x) for x in tree.selection()]
    def _selected_product(self,tree=None):
        tree=tree or self.products; ids=tree.selection(); return ids[0] if ids else None
    def show_detail(self,tree=None):
        pid=self._selected_product(tree)
        if not pid:return
        with self.db.connect() as c:
            p=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone(); snaps=c.execute("SELECT * FROM snapshots WHERE product_id=? ORDER BY captured_at DESC LIMIT 1",(pid,)).fetchone()
        if not p:return
        win=tk.Toplevel(self.root); win.title("单品数据"); win.geometry("850x650");win.transient(self.root)
        cover=p["cover"]
        if cover:
            ttk.Label(win,text="商品主图（加载失败时保留占位区域）",padding=8).pack(anchor="w")
            self._load_cover(cover,win)
        price=snaps["price"] if snaps else None;sold=snaps["sold"] if snaps else None
        ttk.Label(win,text=f"{p['title']}\n店铺：{p['shop_name'] or '—'}    商品 ID：{pid}\n当前价格：{price if price is not None else '—'}    累计已售：{sold if sold is not None else '—'}",font=("Microsoft YaHei UI",11,"bold"),padding=8).pack(anchor="w")
        controls=ttk.Frame(win,padding=(8,2));controls.pack(fill="x")
        day_var=tk.StringVar(value=datetime.now(TZ).date().isoformat());ttk.Label(controls,text="日期").pack(side="left");ttk.Entry(controls,textvariable=day_var,width=12).pack(side="left",padx=4)
        chart=tk.Canvas(win,height=165,bg="white",highlightthickness=1,highlightbackground="#d7dfda");chart.pack(fill="x",padx=8,pady=4)
        tree=self._tree(win,("period","sales","revenue"),("时间","销量增量","模糊销售额（不代表真实成交额）"),12)
        state={"mode":"date"}
        def draw(points):
            chart.delete("all");w=max(1,chart.winfo_width());h=max(1,chart.winfo_height());values=[v for _,v in points if v is not None]
            if not values:chart.create_text(w/2,h/2,text="暂无足够快照计算销量",fill="#718078");return
            low=min(values);high=max(values);span=max(1,high-low);coords=[]
            for i,(_,value) in enumerate(points):
                if value is None:continue
                coords.extend((35+(w-60)*i/max(1,len(points)-1),h-25-(h-50)*(value-low)/span))
            if len(coords)>=4:chart.create_line(*coords,fill=GREEN,width=2,smooth=True)
            for x,y in zip(coords[0::2],coords[1::2]):chart.create_oval(x-3,y-3,x+3,y+3,fill=GREEN,outline="")
        def refresh():
            for iid in tree.get_children():tree.delete(iid)
            with self.db.connect() as c:
                if state["mode"]=="date":
                    day=date.fromisoformat(day_var.get().strip());result=daily_sales(c,pid,day)
                    points=[]
                    for h in result["hours"]:
                        value=h["sales"];price_at=h["price"]
                        revenue=value*price_at if value is not None and price_at is not None else None
                        tree.insert("","end",values=(f"{h['hour']:02d}:00",value if value is not None else "—",f"≈{revenue:.2f}" if revenue is not None else "—"))
                        points.append((h["hour"],value))
                else:
                    days=7 if state["mode"]=="7" else 10;points=[];today=datetime.now(TZ).date()
                    for offset in range(days-1,-1,-1):
                        day=today-timedelta(days=offset);result=daily_sales(c,pid,day);value=result["sales"]
                        tree.insert("","end",values=(day.isoformat(),value if value is not None else "—",f"≈{result['estimated_revenue']:.2f}" if result["estimated_revenue"] is not None else "—"));points.append((day.isoformat(),value))
            draw(points)
        def exact():
            try:date.fromisoformat(day_var.get().strip());state["mode"]="date";refresh()
            except ValueError:messagebox.showerror("日期格式","请输入 YYYY-MM-DD。",parent=win)
        def recent(days):state["mode"]=str(days);refresh()
        ttk.Button(controls,text="查看日期",command=exact).pack(side="left",padx=3);ttk.Button(controls,text="近7天",command=lambda:recent(7)).pack(side="left",padx=3);ttk.Button(controls,text="近10天",command=lambda:recent(10)).pack(side="left",padx=3)
        ttk.Label(win,text="统计以累计已售高水位快照差值计算；缺少完整基线显示为 —。",foreground="#68746d").pack(anchor="w",padx=10,pady=5)
        refresh();win.grab_set()
    def _load_cover(self,url,window):
        placeholder=ttk.Label(window,text="商品图片加载中…",width=32);placeholder.pack(anchor="w",padx=10)
        def work():
            try:
                from PIL import Image,ImageTk
                with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0"}),timeout=8) as r: data=r.read(4_000_000)
                import io
                im=Image.open(io.BytesIO(data)); im.thumbnail((160,160)); photo=ImageTk.PhotoImage(im)
                window.after(0,lambda:(placeholder.config(image=photo,text=""),setattr(placeholder,"image",photo)))
            except Exception:window.after(0,lambda:placeholder.config(text="商品图片暂不可用"))
        threading.Thread(target=work,daemon=True).start()
    def delete_selected(self): self.delete_tree(self.products)
    def delete_tree(self,tree):
        ids=self._selected_ids(tree)
        if not ids:return
        if not messagebox.askyesno("确认删除",f"确定删除 {len(ids)} 个商品及其历史数据？此操作不可恢复。",parent=self.root):return
        for pid in ids:self.db.delete_product(pid)
        self.refresh_all()
    def ignore_selected(self):
        for pid in self._selected_ids(self.failures):
            with self.db.connect() as c:c.execute("UPDATE products SET last_error=NULL WHERE id=?",(pid,))
        self.refresh_all()
    def dedupe(self):
        with self.db.connect() as c:
            products=[dict(p) for p in c.execute("SELECT * FROM products WHERE active=1")]; summaries={p["id"]:product_summary(c,p["id"]) for p in products}
        groups={}
        for p in products:
            s=summaries[p["id"]]; title=" ".join((p["title"] or "").casefold().split())
            keys=(p["shop_name"],title,s["sold"],s["last_hour"])
            if all(v is not None and v!="" for v in keys) and s["price"] is not None:groups.setdefault(keys,[]).append((p,s))
        remove=[]
        for group in groups.values():
            if len({x[0]["id"] for x in group})<2 or len({x[1]["price"] for x in group})<2:continue
            winner=max(group,key=lambda x:x[1]["price"])
            remove.extend(x[0] for x in group if x is not winner)
        if not remove:messagebox.showinfo("智能去重","没有满足全部去重条件的候选商品。",parent=self.root);return
        preview="\n".join(f"{p['title']} · {p['shop_name']} · {s['price']} · {p['id']}" for p,s in [(p,summaries[p['id']]) for p in remove])
        if messagebox.askyesno("确认智能去重",f"以下较低价格候选将删除（含历史）：\n\n{preview}\n\n确认？",parent=self.root):
            for p in remove:self.db.delete_product(p["id"])
            self.refresh_all()
    def auto_expand(self):messagebox.showinfo("自动拓品","目前已明确无法稳定读取未登录店铺商品列表；本版不模拟或绕过登录。可以继续用单条或批量链接导入。",parent=self.root)
    def refresh_logs(self):
        if not hasattr(self,"logs"):return
        for i in self.logs.get_children():self.logs.delete(i)
        filt=self.log_filter.get(); levels={"成功":"success","失败":"error","信息":"info"}
        with self.db.connect() as c:
            q="SELECT * FROM logs"; args=()
            if filt in levels:q+=" WHERE level=?";args=(levels[filt],)
            q+=" ORDER BY id DESC LIMIT 1000"
            for r in c.execute(q,args):self.logs.insert("","end",iid=str(r["id"]),values=(r["time"],r["level"],r["source"],r["title"] or "—",r["product_id"] or "—",r["message"]))
    def log_detail(self,event):
        iid=self.logs.focus()
        if not iid:return
        with self.db.connect() as c:r=c.execute("SELECT * FROM logs WHERE id=?",(iid,)).fetchone()
        if r:messagebox.showinfo("日志详情",f"{r['time']}  {r['level']}\n{r['message']}\n\n{r['details'] or ''}",parent=self.root)
    def clear_logs(self):
        if messagebox.askyesno("清空日志","确定清空全部运行日志？",parent=self.root):
            with self.db.connect() as c:c.execute("DELETE FROM logs")
            self.refresh_logs()
    def export_logs(self):
        path=filedialog.asksaveasfilename(parent=self.root,defaultextension=".csv",filetypes=(("CSV","*.csv"),))
        if not path:return
        with self.db.connect() as c:rows=c.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 10000").fetchall()
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f);w.writerow(rows[0].keys() if rows else ("time","level","source","product_id","title","message","details"));w.writerows([tuple(r) for r in rows])
        messagebox.showinfo("导出完成",path,parent=self.root)
    def save_settings(self):
        try:
            period=max(1,int(self.period.get())); lo=float(self.min_delay.get()); hi=float(self.max_delay.get())
            if not 0.1<=lo<=hi<=60:raise ValueError("间隔必须满足 0.1 ≤ 最小值 ≤ 最大值 ≤ 60 秒")
            self.set_setting("interval_minutes",period); self.set_setting("min_delay",lo); self.set_setting("max_delay",hi)
            self.set_setting("wecom_webhook",self.webhook.get().strip());self.set_setting("wecom_enabled",int(self.wecom_enabled.get()))
            self.status.config(text="设置已保存")
        except Exception as e:messagebox.showerror("设置错误",str(e),parent=self.root)
    def test_wecom(self):
        url=self.webhook.get().strip()
        if not valid_webhook(url):messagebox.showerror("Webhook 错误","Webhook 格式不正确。",parent=self.root);return
        threading.Thread(target=lambda:self._send_test(url),daemon=True).start()
    def _send_test(self,url):
        try:
            send_text(url,"红薯雷达测试通知：连接正常。");self.db.log("success","企业微信测试通知已发送",source="wecom")
            self.root.after(0,lambda:messagebox.showinfo("发送成功","测试通知已发送。",parent=self.root))
        except Exception as e:
            message=safe_error(e,url);self.db.log("error",f"企业微信测试通知失败：{message}",source="wecom")
            self.root.after(0,lambda:messagebox.showerror("发送失败",message,parent=self.root))
    def choose_shops(self):
        with self.db.connect() as c:shops=[x[0] for x in c.execute("SELECT DISTINCT shop_name FROM products WHERE active=1 AND shop_name IS NOT NULL")]
        win=tk.Toplevel(self.root);win.title("设置通知店铺");win.geometry("420x480")
        state={s:tk.BooleanVar(value=s in self.setting("wecom_shops","").splitlines()) for s in shops}
        top=ttk.Frame(win,padding=6);top.pack(fill="x")
        ttk.Button(top,text="全选",command=lambda:[v.set(True) for v in state.values()]).pack(side="left");ttk.Button(top,text="取消全选",command=lambda:[v.set(False) for v in state.values()]).pack(side="left",padx=5)
        query=tk.StringVar();ttk.Entry(top,textvariable=query).pack(side="right",fill="x",expand=True,padx=(8,0))
        body=ttk.Frame(win);body.pack(fill="both",expand=True)
        canvas=tk.Canvas(body,highlightthickness=0);scroll=ttk.Scrollbar(body,orient="vertical",command=canvas.yview);inner=ttk.Frame(canvas)
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window=canvas.create_window((0,0),window=inner,anchor="nw");canvas.configure(yscrollcommand=scroll.set)
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(canvas_window,width=e.width));canvas.pack(side="left",fill="both",expand=True);scroll.pack(side="right",fill="y")
        def render(*_):
            for child in inner.winfo_children():child.destroy()
            term=query.get().casefold()
            for s,v in state.items():
                if not term or term in s.casefold():ttk.Checkbutton(inner,text=s,variable=v).pack(anchor="w")
        query.trace_add("write",render);render()
        ttk.Button(win,text="保存",command=lambda:(self.set_setting("wecom_shops","\n".join(s for s,v in state.items() if v.get())),win.destroy())).pack(pady=8)
    def copy_mcp(self):
        exe=Path(__file__).with_name("红薯雷达MCP.exe"); script=Path(__file__).with_name("mcp_server.py")
        command=str(exe) if exe.exists() else "python"
        args=[] if exe.exists() else [str(script)]
        text=json.dumps({"mcpServers":{"xhs-sales-monitor":{"command":command,"args":args}}},ensure_ascii=False,indent=2)
        self.root.clipboard_clear();self.root.clipboard_append(text);messagebox.showinfo("已复制",f"MCP 只读服务配置已复制。命令：\n{command} {' '.join(args)}\n连接后调用 tools/list 验证。",parent=self.root)
    def cleanup(self):
        if not messagebox.askyesno("一键清理","清理90天前快照（每个商品保留最近一条历史基线）和30天前日志？",parent=self.root):return
        try:
            before=self.db.path.stat().st_size; result=self.db.cleanup(); freed=max(0,before-result["bytes"])
            messagebox.showinfo("清理完成",f"删除快照：{result['snapshots']}\n删除日志：{result['logs']}\n保留历史基线：按商品各1条\n释放空间：{freed/1024/1024:.2f} MB",parent=self.root);self.refresh_all()
        except Exception as e:messagebox.showerror("清理失败",str(e),parent=self.root)
    def close(self):
        self.service.stop()
        for collector in list(self._verification_collectors):
            try:collector.close()
            except Exception:pass
        self.root.destroy()

def main():
    root=tk.Tk();RadarApp(root);root.mainloop()
if __name__=="__main__":main()
