# -*- mode: python ; coding: utf-8 -*-
import os
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    load_version_info_from_text_file,
)

block_cipher = None

# Use the same version as the app (APP_VERSION in converter.py) for the executable's
# Windows metadata; publisher and product details live in version_info.txt.
spec_dir = Path(SPECPATH)
app_version = re.search(r'^APP_VERSION = "([^"]+)"',
                        (spec_dir / 'converter.py').read_text(encoding='utf-8'), re.M).group(1)
parts = [int(part) for part in app_version.split('.')]
windows_version = tuple((parts + [0, 0, 0, 0])[:4])
# Release file name, e.g. TxTEPUBtoPDF-Converter.v2.1.exe
EXE_NAME = 'TxTEPUBtoPDF-Converter.v' + app_version

version_info = load_version_info_from_text_file(str(spec_dir / 'version_info.txt'))
version_info.ffi = FixedFileInfo(filevers=windows_version, prodvers=windows_version)
version_strings = {'FileVersion': '.'.join(map(str, windows_version)),
                   'ProductVersion': app_version,
                   'OriginalFilename': EXE_NAME + '.exe'}
for info in version_info.kids:
    if isinstance(info, StringFileInfo):
        for table in info.kids:
            for entry in table.kids:
                if entry.name in version_strings:
                    entry.val = version_strings[entry.name]

# --- GTK/MSYS2 DLLs for WeasyPrint (layout-preserving EPUB -> PDF) ---
gtk_folder = os.environ.get('GTK_FOLDER', '')
msys2_bin_candidates = [
    os.path.join(gtk_folder, 'bin') if gtk_folder else '',
    r'C:\msys64\mingw64\bin',
    r'C:\msys64\ucrt64\bin',
    r'D:\a\_temp\msys64\mingw64\bin',
]
msys2_bin = None
for candidate in msys2_bin_candidates:
    if candidate and os.path.exists(candidate):
        msys2_bin = candidate
        break

# WeasyPrint loads these by name at runtime; PyInstaller pulls in their dependencies.
gtk_binaries = []
if msys2_bin:
    os.environ['PATH'] = msys2_bin + os.pathsep + os.environ.get('PATH', '')
    for dll in ('libgobject-2.0-0.dll', 'libpango-1.0-0.dll', 'libpangoft2-1.0-0.dll',
                'libharfbuzz-0.dll', 'libharfbuzz-subset-0.dll', 'libfontconfig-1.dll'):
        dll_path = os.path.join(msys2_bin, dll)
        if os.path.exists(dll_path):
            gtk_binaries.append((dll_path, '.'))

a = Analysis(['converter.py'],
             pathex=[],
             binaries=gtk_binaries,
             datas=[],
             hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
                            'engine', 'epub_layout_pdf', 'dll_guard'],
             hookspath=[],
             # Stray DLL protection must run before Qt is imported.
             runtime_hooks=['rthook_dll_guard.py'],
             excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy', 'IPython',
                       'PyQt5', 'PyQt6', 'fitz', 'pymupdf'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)
pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)
exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name=EXE_NAME,
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          runtime_tmpdir=None,
          console=False,
          version=version_info)
