# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files
from PyInstaller.building.datastruct import Tree

root=Path(SPECPATH)
pw_datas,pw_bins,pw_hidden=collect_all("playwright")
mcp_datas=collect_data_files("mcp",includes=["py.typed"])
mcp_bins=[]
mcp_hidden=[]
icon=str(root/"assets"/"app-icon.ico")
browser_dir=root/".pw-browsers"
gui_datas=pw_datas+[(str(root/"assets"),"assets")]
if browser_dir.exists(): gui_datas += Tree(str(browser_dir),prefix=".pw-browsers")
common=dict(pathex=[str(root)],hookspath=[],runtime_hooks=[],excludes=[],noarchive=False)

gui_analysis=Analysis([str(root/"app.py")],binaries=pw_bins,datas=gui_datas,hiddenimports=pw_hidden,**common)
gui_pyz=PYZ(gui_analysis.pure)
gui_exe=EXE(gui_pyz,gui_analysis.scripts,gui_analysis.binaries,gui_analysis.datas,
            name="红薯雷达",debug=False,bootloader_ignore_signals=False,strip=False,upx=True,
            console=False,disable_windowed_traceback=False,icon=icon)

mcp_analysis=Analysis([str(root/"mcp_server.py")],binaries=mcp_bins,datas=mcp_datas,hiddenimports=mcp_hidden,**common)
mcp_pyz=PYZ(mcp_analysis.pure)
mcp_exe=EXE(mcp_pyz,mcp_analysis.scripts,mcp_analysis.binaries,mcp_analysis.datas,
            name="红薯雷达MCP",debug=False,bootloader_ignore_signals=False,strip=False,upx=True,
            console=True,icon=icon)
