# -*- mode: python ; coding: utf-8 -*-
"""TriangleAlpha Master — onefile 单文件"""

a = Analysis(
    ['src/master/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/master/app/resource', 'master/app/resource'),
    ],
    hiddenimports=[
        'PyQt6.sip',
        'qfluentwidgets',
        'master.app.view.main_window',
        'master.app.view.bigscreen_interface',
        'master.app.view.account_interface',
        'master.app.view.history_interface',
        'master.app.view.log_interface',
        'master.app.view.setting_interface',
        'master.app.common.config',
        'master.app.common.style_sheet',
        'master.app.core.node_manager',
        'master.app.core.tcp_commander',
        'master.app.core.udp_listener',
        'master.app.core.log_receiver',
        'master.app.core.account_pool',
        'master.app.components.stat_card',
        'common.protocol',
        'common.models',
        'psutil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='TriangleAlpha-Master',
    debug=False, strip=False, upx=False,
    console=False,
    icon='src/master/app/resource/icon.ico',
)
