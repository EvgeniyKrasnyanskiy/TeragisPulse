# -*- mode: python ; coding: utf-8 -*-
import platform
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['identification']

is_win = platform.system() == 'Windows'

if is_win:
    # На Windows исключаем линуксовый site-packages, чтобы не было конфликтов PIL (ImportError _imaging)
    pathex_dirs = ['..']
else:
    # На Linux подключаем 32-битный интерпретатор
    pathex_dirs = ['../python/lib/python3.10/site-packages', '..']

# Pillow собирается на обеих платформах
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['tnotif.py'],
    pathex=pathex_dirs,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TeragisNotifier',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TeragisNotifier',
)
