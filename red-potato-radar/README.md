# 红薯雷达

红薯雷达是一个本地运行的 Windows 桌面原型，用于手动导入公开商品链接、保存可读取的商品快照、按累计销量快照差值查看趋势，并通过企业微信机器人发送店铺时报。数据库、日志和浏览器登录状态都留在本机。软件界面提示仅供学习交流；请遵守平台规则、隐私要求和适用法律。

## 目前包含

- Tkinter/ttk 桌面窗口、看板、商品添加、失败/下架列表、店铺分析、设置和运行日志。
- SQLite WAL 数据库、旧表字段迁移、快照与错误持久化、90天快照/30天日志清理（为每个商品保留一条清理期前的基线）。
- 商品 ID、短链和链接识别；小红书详情接口字段解析；接口、持久化静默浏览器、临时 Profile 可见浏览器的逐商品回退顺序。
- 上海时区自然边界调度、461 熔断、临时失败重试一次、明确下架识别、销量高水位统计。
- 企业微信机器人通知及 stdio 只读 MCP 服务。
- Windows PyInstaller 单文件构建脚本和双 EXE spec。

## 已知限制

小红书网页和接口并非稳定的公开 API，字段可能随平台更新变化，也可能要求有效登录状态或受到访问限制。程序不会把异常响应当成零销量，也不会尝试绕过平台验证。首次使用可从“设置 → 打开浏览器处理验证”在本机打开持久化浏览器；采集是否成功仍取决于平台当前响应。

提示词列出的自动拓品依赖店铺商品列表。提示词本身说明未登录桌面店铺商品列表不可用，因此本版本明确将该按钮保留为限制提示；没有可靠页面/API样本时，不会猜测商品列表或自动加入商品。MCP 以 Python 脚本形式可运行；独立 MCP EXE 的 Windows stdio 兼容性必须在目标 Windows/MCP 客户端上实测。可见浏览器回退也需要本机桌面会话。

当前开发环境是 Linux，不能在这里生成或实测 Windows EXE。源码、SQLite、API 解析及 MCP 工具可在此环境检查；`build.bat` 和 `.spec` 需在 Windows 上运行。构建时要能下载 Python 包和 Playwright Chromium。主程序为无控制台窗口；MCP EXE 保留控制台标准流，以供 MCP stdio 通信。

## Windows 安装与启动

1. 安装 Python 3.11 或 3.12，并确保 `py` 启动器可用。
2. 解压项目目录，双击 `build.bat`。脚本创建 `.venv`，安装 `requirements.txt`、Playwright Chromium 并运行 PyInstaller。
3. 构建后检查 `dist/红薯雷达.exe` 和 `dist/红薯雷达MCP.exe`。将两个文件放在同一个文件夹，运行主程序。
4. 首次运行会在 EXE 同目录创建 `monitor.db`。若目录没有写权限，数据库回退到 `文档/RedPotatoRadar`。可设置 `RED_POTATO_RADAR_HOME` 指定数据目录。

独立从源码运行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

运行只读 MCP 服务：

```powershell
python mcp_server.py
```

MCP stdio 模式由宿主进程启动；不要在同一终端并行输出普通日志到 stdout。

## MCP 工具

服务以 SQLite `mode=ro` 打开现有数据库，注册 `list_shops`、`list_products`、`get_product_sales`、`get_shop_sales`、`get_product_ranking`、`get_shop_ranking`、`search_products` 和 `get_overview`。所有查询只读并返回统计口径和数据时间。GUI 的“复制 MCP 配置说明”会给出当前脚本或 EXE 路径。

## 统计口径

今日、昨日、小时和日期区间销量以累计已售快照的可信高水位差值计算。缺少可用基线返回“—”/`null`；新读数低于历史高水位时不产生负销量。模糊销售额为销量增量乘快照价格，仅供趋势参考。自然日和时间戳使用 `Asia/Shanghai`。

## 当前环境验证

已在 Linux/Python 3.12 完成 Python 语法检查、临时 SQLite 数据库与旧表迁移检查、销量基线/回退/清理检查、采集编排模拟、源码 MCP 客户端握手，以及 PyInstaller Linux 单文件 MCP 的 `tools/list` 与 `get_overview` 调用。Linux MCP 产物仅用于验证后已删除。当前云环境没有图形显示，因此 Tk 窗口和 Chrome/Edge 可见回退未实测；也没有对真实小红书商品或企业微信 Webhook 发请求。Windows EXE 仍须在 Windows 上运行 `build.bat` 并实机验收；构建脚本完成不等于 Windows EXE 已实测。
