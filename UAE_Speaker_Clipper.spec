# PyInstaller spec for the standalone Windows executable.
# FFmpeg and yt-dlp are downloaded by the GitHub Actions workflow into bin/.
from PyInstaller.utils.hooks import collect_all

pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all('PySide6')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('bin/ffmpeg.exe', 'bin'),
        ('bin/yt-dlp.exe', 'bin'),
    ],
    datas=pyside_datas,
    hiddenimports=pyside_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='UAE_Speaker_Clipper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)
