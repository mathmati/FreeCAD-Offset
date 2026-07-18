# SPDX-License-Identifier: MIT
"""Workbench registration for the OffsetTool addon.

Importing this module (auto-discovered by FreeCAD's Modern-layout addon
loader from Mod/<addon>/freecad/OffsetToolWB/) registers the workbench
with Gui.addWorkbench(...). No network access or other expensive work
happens at import/startup time -- the tool only acts on explicit user
action (activating the command, then clicking/dragging a face).
"""
import os

import FreeCADGui as Gui


class OffsetWorkbench(Gui.Workbench):
    MenuText = "Offset"
    ToolTip = "SketchUp-style offset of a planar face's boundary"
    Icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Resources",
        "Icons",
        "offsettool.svg",
    )

    def Initialize(self):
        # Import side effect registers the command with Gui.addCommand.
        from . import commands

        commands.register()
        self.appendToolbar("Offset", ["OffsetTool_Offset"])
        self.appendMenu("Offset", ["OffsetTool_Offset"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"  # exact string, mandatory, do not change


Gui.addWorkbench(OffsetWorkbench())
