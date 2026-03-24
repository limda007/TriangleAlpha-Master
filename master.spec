# ruff: noqa: F821
"""TriangleAlpha Master — onefile 单文件"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(SPECPATH).resolve()
RESOURCE_DIR = ROOT_DIR / 'src' / 'master' / 'app' / 'resource'


def _build_macos_icon() -> str | None:
    """基于现有 PNG 生成 macOS app bundle 所需的 .icns。"""
    if sys.platform != 'darwin':
        return None

    source_png = RESOURCE_DIR / 'icon_512.png'
    if not source_png.exists():
        return None

    output_dir = ROOT_DIR / 'build' / 'macos'
    output_dir.mkdir(parents=True, exist_ok=True)
    icon_path = output_dir / 'icon.icns'
    iconutil = '/usr/bin/iconutil'
    sips = '/usr/bin/sips'

    with tempfile.TemporaryDirectory(prefix='trianglealpha-iconset-') as tmp_dir:
        iconset_dir = Path(tmp_dir) / 'TriangleAlpha.iconset'
        iconset_dir.mkdir()

        for size in (16, 32, 128, 256, 512):
            out_path = iconset_dir / f'icon_{size}x{size}.png'
            subprocess.run(
                [sips, '-z', str(size), str(size), str(source_png), '--out', str(out_path)],
                check=True,
                capture_output=True,
                text=True,
            )

            retina_size = size * 2
            retina_path = iconset_dir / f'icon_{size}x{size}@2x.png'
            subprocess.run(
                [sips, '-z', str(retina_size), str(retina_size), str(source_png), '--out', str(retina_path)],
                check=True,
                capture_output=True,
                text=True,
            )

        subprocess.run(
            [iconutil, '-c', 'icns', str(iconset_dir), '-o', str(icon_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    return str(icon_path)


from PyInstaller.utils.hooks import collect_all

httpx_datas, httpx_binaries, httpx_hiddenimports = collect_all('httpx')
httpcore_datas, httpcore_binaries, httpcore_hiddenimports = collect_all('httpcore')

macos_icon = _build_macos_icon()
exe_icon = 'src/master/app/resource/icon.ico' if sys.platform == 'win32' else None

a = Analysis(
    ['src/master/main.py'],
    pathex=['src'],
    binaries=[] + httpx_binaries + httpcore_binaries,
    datas=[
        ('src/master/app/resource', 'master/app/resource'),
        ('pyproject.toml', '.'),
    ] + httpx_datas + httpcore_datas,
    hiddenimports=[
        'PyQt6.sip',
        'qfluentwidgets',
        'master.app.view.main_window',
        'master.app.view.bigscreen_interface',
        'master.app.view.account_interface',
        'master.app.view.history_interface',
        'master.app.view.log_interface',
        'master.app.view.setting_interface',
        'master.app.view.help_interface',
        'markdown',
        'markdown.extensions.tables',
        'markdown.extensions.fenced_code',
        'master.app.common.config',
        'master.app.common.style_sheet',
        'master.app.core.node_manager',
        'master.app.core.tcp_commander',
        'master.app.core.udp_listener',
        'master.app.core.log_receiver',
        'master.app.core.account_db',
        'master.app.core.kami_db',
        'master.app.core.kami_client',
        'master.app.view.kami_interface',
        'master.app.components.stat_card',
        'common.protocol',
        'common.models',
        'psutil',
        'httpx',
        'certifi',
        'master.app.core.platform_syncer',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    noarchive=False,
)
pyz = PYZ(a.pure)
if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='TriangleAlpha-Master',
        debug=False,
        strip=False,
        upx=False,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='TriangleAlpha-Master',
    )
    app = BUNDLE(
        coll,
        name='TriangleAlpha-Master.app',
        icon=macos_icon,
        bundle_identifier='com.trianglealpha.master',
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name='TriangleAlpha-Master',
        debug=False, strip=False, upx=False,
        console=False,
        icon=exe_icon,
    )
