from pathlib import Path
import os
import sys
import shutil
from PySide6.QtCore import QLibraryInfo
import PySide6
import shiboken6

root = Path(SPECPATH)
# The same explicit public-resource inventory is used by both ZIP packagers.
# Add the root so this also works when PyInstaller is launched elsewhere.
sys.path.insert(0, str(root))
from scripts.public_distribution import distribution_resources

public_resources = distribution_resources(root)
build_mode = os.environ.get('QBOARD_BUILD_MODE', 'onefile').lower()
if build_mode not in {'onefile', 'onedir'}:
    raise ValueError('QBOARD_BUILD_MODE must be onefile or onedir')
# Resolve DLLs only from this interpreter/Qt and Windows, never unrelated tools.
windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
os.environ['PATH'] = os.pathsep.join(str(p) for p in (
    Path(PySide6.__file__).parent, Path(shiboken6.__file__).parent,
    Path(sys.executable).parent, Path(sys.base_prefix), windows / 'System32', windows,
))
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=[],
             datas=[(str(path), path.relative_to(root).parent.as_posix())
                    for path in public_resources] + [
                 (str(Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"),
                  "PySide6/translations")],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'numpy', 'pytest'], noarchive=False)
allowed_plugins = {'qwindows.dll', 'qoffscreen.dll', 'qico.dll'}
unused_qt = {'qt6pdf.dll', 'qt6virtualkeyboard.dll', 'qt6quick.dll', 'qt6opengl.dll', 'qt6svg.dll'}

def keep_binary(entry):
    name = entry[0].replace('\\', '/').lower()
    base = name.rsplit('/', 1)[-1]
    if '/plugins/' in name and base not in allowed_plugins:
        return False
    if base in unused_qt or base.startswith('qt6qml'):
        return False
    if base in {'libssl-3-x64.dll', 'libcrypto-3-x64.dll', 'opengl32sw.dll'}:
        return False
    return True

a.binaries = [entry for entry in a.binaries if keep_binary(entry)]
pyz = PYZ(a.pure)
exe_options = dict(name='SimpleBoard', debug=False, bootloader_ignore_signals=False,
                   strip=False, upx=False, console=bool(os.environ.get('QBOARD_DEBUG_CONSOLE')),
                   version=str(root / 'version_info.txt'), icon=str(root / 'assets' / 'simpleboard.ico'))
if build_mode == 'onefile':
    # The runtime, README and licenses travel inside this single executable.
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_options)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_options)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SimpleBoard')
    destination = Path(DISTPATH) / 'SimpleBoard'
    for source in public_resources:
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
