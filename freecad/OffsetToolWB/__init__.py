# SPDX-License-Identifier: MIT
"""OffsetTool workbench package (Modern namespaced layout).

Importing this package has no side effects beyond making the submodules
importable -- workbench/command registration happens in init_gui.py,
which is imported once by FreeCAD's Addon Manager (or a Mod/ install)
when the GUI loads this addon. core.py and controller.py stay importable
under plain freecadcmd (no PySide/pivy imports at module level there).
"""
