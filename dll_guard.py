"""Stray DLL protection for the one-file Windows build.

Windows looks for a DLL in the exe's own folder before System32. When the exe sits
in a busy folder such as Downloads, a stray copy of a system DLL that Qt needs
(d3d11.dll, dwrite.dll, … left behind by game mods or graphics wrappers) gets
loaded instead of the real one, and the app dies with "DLL load failed while
importing QtGui".

``protect()`` loads the genuine copy of every DLL that has a look-alike next to the
exe — the bundled one if the app ships it, otherwise the System32 one — by full
path. Windows reuses an already-loaded DLL of the same name, so the stray file is
never picked up. It runs silently from a PyInstaller runtime hook, before Qt is imported.
"""
import os
import sys

pinned: list[tuple[str, str]] = []   # (stray file name, genuine path loaded instead)


def protect() -> list[tuple[str, str]]:
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or pinned:
        return pinned
    bundle = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if not bundle or os.path.normcase(os.path.abspath(bundle)) == os.path.normcase(exe_dir):
        return pinned  # one-folder build: the exe folder *is* the bundle
    try:
        strays = [n for n in os.listdir(exe_dir) if n.lower().endswith(".dll")]
    except OSError:
        return pinned
    if not strays:
        return pinned

    bundled = {}
    for root, _dirs, files in os.walk(bundle):
        for name in files:
            if name.lower().endswith(".dll"):
                bundled.setdefault(name.lower(), os.path.join(root, name))
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")

    import ctypes
    for name in strays:
        genuine = bundled.get(name.lower())
        if genuine is None and os.path.isfile(os.path.join(system32, name)):
            genuine = os.path.join(system32, name)
        if genuine is None:
            continue  # not a name the app could ever load
        try:
            ctypes.WinDLL(genuine)
            pinned.append((name, genuine))
        except OSError:
            pass
    return pinned
