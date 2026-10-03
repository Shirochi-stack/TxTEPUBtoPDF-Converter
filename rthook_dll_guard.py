# PyInstaller runtime hook: guard against stray DLLs next to the exe before Qt loads.
import dll_guard

dll_guard.protect()
