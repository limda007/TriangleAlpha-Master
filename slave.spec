# -*- mode: python ; coding: utf-8 -*-
"""TriangleAlpha Slave — onefile 单文件 (GUI 版)"""

a = Analysis(
    ['src/slave/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'psutil',
        'psutil._pswindows',
        'psutil._psutil_windows',
        'ctypes',
        'ctypes.wintypes',
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'qfluentwidgets'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='TriangleAlpha-Slave',
    debug=False, strip=False, upx=False,
    console=False, icon=None,
)
