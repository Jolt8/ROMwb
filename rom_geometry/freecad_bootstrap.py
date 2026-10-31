"""Make a plain `python script.py` run under FreeCAD's bundled Python.

FreeCAD 1.1 is built against Python 3.11 (python311.dll). If a script is
started with any other interpreter, it is re-launched with FreeCAD's own
python.exe, the same trick used in FVMFramework's new_meshing_script.py.

This module must not import FreeCAD itself, because it runs before FreeCAD's
directories are on sys.path.
"""

import os
import subprocess
import sys


DEFAULT_FREECAD_BIN = r"C:\Program Files\FreeCAD 1.1\bin"


def ensure_running_under_freecad_python(script_path):
    """Re-launch `script_path` with FreeCAD's python if needed, then put
    FreeCAD's bin and lib folders on sys.path so `import FreeCAD` works."""
    already_inside_freecad = "FreeCAD" in sys.modules
    if already_inside_freecad:
        return

    freecad_bin = os.environ.get("FREECAD_BIN", DEFAULT_FREECAD_BIN)
    freecad_lib = os.path.join(os.path.dirname(freecad_bin), "lib")
    freecad_python = os.path.join(freecad_bin, "python.exe")

    running_under_freecad_python = os.path.normcase(sys.executable) == os.path.normcase(freecad_python)
    if not running_under_freecad_python:
        if not os.path.exists(freecad_python):
            raise RuntimeError(f"FreeCAD's Python was not found at '{freecad_python}'. Set FREECAD_BIN to FreeCAD's bin folder.")
        result = subprocess.run([freecad_python, os.path.abspath(script_path)] + sys.argv[1:])
        sys.exit(result.returncode)

    if hasattr(os, "add_dll_directory") and os.path.isdir(freecad_bin):
        os.add_dll_directory(freecad_bin)

    for freecad_path in (freecad_bin, freecad_lib):
        if freecad_path not in sys.path:
            sys.path.append(freecad_path)
