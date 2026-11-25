# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller single-file (onefile) spec for 砚席 · 图书馆智能选座 (YanxiSeatReserver.exe).
Build command:
    uv run pyinstaller --noconfirm yanxi.spec
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT_DIR = Path(SPECPATH).resolve()

datas = [
    (str(ROOT_DIR / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT_DIR / "config.example.json"), "."),
]
binaries = []
hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "h11",
    "httpx",
    "httpcore",
    "certifi",
    "loguru",
    "pydantic",
    "pydantic_core",
    "clr_loader",
    "pythonnet",
]

for pkg_name in ("playwright", "webview", "clr_loader", "pythonnet"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg_name)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass

a = Analysis(
    [str(ROOT_DIR / "main.py")],
    pathex=[str(ROOT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["docx", "pptx", "pytest", "lxml", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="YanxiSeatReserver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
