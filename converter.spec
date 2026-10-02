# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct,
                                                 VSVersionInfo)

block_cipher = None

PUBLISHER = 'shirochi-stack'
APP_NAME = 'File Converter'
VERSION = (2, 1, 0, 0)
VERSION_STR = '.'.join(map(str, VERSION))

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

# Publisher / product details shown under Properties > Details on the .exe
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION, prodvers=VERSION, mask=0x3f, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', PUBLISHER),
            StringStruct('FileDescription', APP_NAME + ' - TXT / PDF / EPUB converter and chapter splitter'),
            StringStruct('FileVersion', VERSION_STR),
            StringStruct('InternalName', 'FileConverter'),
            StringStruct('LegalCopyright', 'Copyright (c) ' + PUBLISHER),
            StringStruct('OriginalFilename', 'FileConverter.exe'),
            StringStruct('ProductName', APP_NAME),
            StringStruct('ProductVersion', VERSION_STR),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

a = Analysis(['converter.py'],
             pathex=[],
             binaries=gtk_binaries,
             datas=[],
             hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
                            'engine', 'epub_layout_pdf'],
             hookspath=[],
             runtime_hooks=[],
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
          name='FileConverter',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          runtime_tmpdir=None,
          console=False,
          version=version_info)
