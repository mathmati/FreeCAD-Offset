# SPDX-License-Identifier: MIT
# UNVERIFIED: written 2026-07-18 for manual GUI runs; never executed in the
# build environment (no display available). Run it on a desktop session and
# treat this header as accurate until someone has run it and deleted this line.
"""verify/drivers/offset_driver.py -- OffsetTool GUI driver (manual runs).

Drives the REAL OffsetCommand end to end against a real 3D view:

  1. builds a loose 20x20 Part::Feature face and selects its Face1 with the
     real Gui.Selection API (so Activated() arms immediately, exactly like
     a user preselecting a face and hitting the toolbar button);
  2. invokes the real OffsetCommand class (Activated(), what a toolbar
     click runs -- no mocked command);
  3. types "-2.5" + Enter as genuinely synthetic Qt input: real QKeyEvent
     objects dispatched through QApplication.notify(), which runs the
     command's installed application-level event filter (the same dispatch
     a physical keyboard takes);
  4. asserts an OffsetInner (exact 15x15 = 225 area) and OffsetRing were
     committed, and screenshots the 3D view.

Run on a desktop session (a window flashes open):

    freecad verify/drivers/offset_driver.py

Prints PASS/FAIL, drops ``verify/out/offset_committed.png``, and writes a
machine-readable ``verify/out/offset_driver.result.txt`` -- use the result
file for CI, process exit codes from a GUI startup script are not reliable.
"""
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_OUT_DIR = os.path.join(_REPO_ROOT, "verify", "out")
_RESULT_PATH = os.path.join(_OUT_DIR, "offset_driver.result.txt")

try:
    import freecad  # FreeCAD's own namespace package
    freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
except ImportError:
    sys.path.insert(0, _REPO_ROOT)

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
import Part  # noqa: E402
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

from freecad.OffsetToolWB.commands import OffsetCommand  # noqa: E402


def pump(n=10):
    """Let Qt/Coin process pending events (selection, callbacks, redraws)."""
    app = QtWidgets.QApplication.instance()
    for _ in range(n):
        app.processEvents()


def send_key(key, text=""):
    """Dispatch a real QKeyEvent through the application (runs the command's
    installed event filter, like a physical keypress)."""
    app = QtWidgets.QApplication.instance()
    mw = Gui.getMainWindow()
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier, text)
    app.sendEvent(mw, event)
    pump(3)


def main():
    os.makedirs(_OUT_DIR, exist_ok=True)
    failures = []

    doc = App.newDocument("OffsetGuiVerify")
    src = doc.addObject("Part::Feature", "SourceFace")
    v = [App.Vector(0, 0, 0), App.Vector(20, 0, 0),
         App.Vector(20, 20, 0), App.Vector(0, 20, 0)]
    src.Shape = Part.Face(Part.makePolygon(v + [v[0]]))
    doc.recompute()
    Gui.ActiveDocument.ActiveView.viewAxonometric()
    Gui.SendMsgToActiveView("ViewFit")
    pump()

    # 1. select the face the real way, then invoke the real command.
    Gui.Selection.addSelection(src, "Face1")
    pump()
    cmd = OffsetCommand()
    cmd.Activated()
    pump()

    if cmd.controller is None or not cmd.controller.active:
        failures.append("command did not arm on the preselected face")

    # 2. type "-2.5" and press Enter through the real Qt dispatch.
    for key, text in ((QtCore.Qt.Key_Minus, "-"), (QtCore.Qt.Key_2, "2"),
                      (QtCore.Qt.Key_Period, "."), (QtCore.Qt.Key_5, "5")):
        send_key(key, text)
    if cmd.controller is not None and cmd.controller.typed_buffer != "-2.5":
        failures.append("typed buffer is %r, expected '-2.5' (digit-key race?)"
                        % (cmd.controller.typed_buffer,))
    send_key(QtCore.Qt.Key_Return, "\r")
    pump()

    # 3. assert the commit landed.
    names = [o.Name for o in doc.Objects]
    inner = [o for o in doc.Objects if o.Name.startswith("OffsetInner")]
    ring = [o for o in doc.Objects if o.Name.startswith("OffsetRing")]
    if not inner:
        failures.append("no OffsetInner committed (objects: %r)" % names)
    else:
        area = inner[0].Shape.Area
        if abs(area - 225.0) > 1e-9:
            failures.append("OffsetInner area %r, expected 225" % area)
    if not ring:
        failures.append("no OffsetRing committed (objects: %r)" % names)

    shot = os.path.join(_OUT_DIR, "offset_committed.png")
    try:
        Gui.ActiveDocument.ActiveView.saveImage(shot, 1280, 1024, "Current")
    except Exception as exc:
        failures.append("screenshot failed: %s" % exc)

    with open(_RESULT_PATH, "w") as f:
        f.write("PASS\n" if not failures else "FAIL\n" + "\n".join(failures) + "\n")

    if failures:
        print("FAIL")
        for f_ in failures:
            print("  - %s" % f_)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        try:
            os.makedirs(_OUT_DIR, exist_ok=True)
            with open(_RESULT_PATH, "w") as f:
                f.write("FAIL\ndriver crashed:\n" + traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)
