# ruff: noqa: F821
"""TriangleAlpha Slave — onefile 单文件 (GUI 版)"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(SPECPATH).resolve()
RESOURCE_DIR = ROOT_DIR / 'src' / 'slave' / 'resource'


def _build_macos_icon() -> str | None:
    """基于现有 PNG 生成 macOS app bundle 所需的 .icns。"""
    if sys.platform != 'darwin':
        return None

    source_png = RESOURCE_DIR / 'icon_512.png'
    if not source_png.exists():
        return None

    output_dir = ROOT_DIR / 'build' / 'macos-slave'
    output_dir.mkdir(parents=True, exist_ok=True)
    icon_path = output_dir / 'icon.icns'
    iconutil = '/usr/bin/iconutil'
    sips = '/usr/bin/sips'

    with tempfile.TemporaryDirectory(prefix='trianglealpha-slave-iconset-') as tmp_dir:
        iconset_dir = Path(tmp_dir) / 'TriangleAlphaSlave.iconset'
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

from PyInstaller.utils.hooks import collect_all  # noqa: E402

pydantic_datas, pydantic_binaries, pydantic_hiddenimports = collect_all('pydantic')

macos_icon = _build_macos_icon()
exe_icon = 'src/slave/resource/icon.ico' if sys.platform == 'win32' else None

a = Analysis(
    ['src/slave/main.py'],
    pathex=['src'],
    binaries=[] + pydantic_binaries,
    datas=[
        ('src/slave/resource', 'slave/resource'),
        ('pyproject.toml', '.'),
    ] + pydantic_datas,
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
        'pydantic',
        'pydantic.fields',
        'common.protocol',
        'common.app_version',
        'common_app_version',
        'slave.logging_utils',
        'slave.runtime_paths',
        'slave.state_store',
        'slave.models',
        'slave.ipc_receiver',
        'slave.process_watcher',
        'slave.account_syncer',
        'slave.log_reporter',
        'slave.command_handler',
        'slave.heartbeat',
        'slave.gpu_monitor',
        'slave.auto_setup',
    ] + pydantic_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'qfluentwidgets'],
    noarchive=False,
)
pyz = PYZ(a.pure)
if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='TriangleAlpha-Slave',
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
        name='TriangleAlpha-Slave',
    )
    app = BUNDLE(
        coll,
        name='TriangleAlpha-Slave.app',
        icon=macos_icon,
        bundle_identifier='com.trianglealpha.slave',
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name='TriangleAlpha-Slave',
        debug=False, strip=False, upx=False,
        console=False,
    )
