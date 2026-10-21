# Build on Windows to produce a Windows executable.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["EDrumBonanza.py"],
    pathex=[],
    binaries=[],
    datas=[("default_sounds", "default_sounds"), ("drumlogo.png", ".")]
    + collect_data_files("imageio_ffmpeg"),
    hiddenimports=["mido.backends.rtmidi"],
    hookspath=[],
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
    name="EDrumBonanza",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
