# SPDX-License-Identifier: MIT
"""FreeCAD Gui.Command for the OffsetTool workbench.

Interaction model (SketchUp-style, mirroring the PushPull addon's
command idiom):

1. Activate the "Offset" command (toolbar/menu). If a single planar
   face is already selected the normal FreeCAD way (Gui.Selection), the
   session starts immediately.
2. Otherwise click a planar face in the 3D view (uses the live
   preselection FreeCAD's selection system already computes on hover --
   no custom picking code).
3. Move the mouse (holding the button, or click once and move freely):
   the signed distance from the cursor to the face's boundary is tracked
   and shown in the status bar (negative inside the face = inward).
   OR type a number (digits, '.', '-') for an exact distance.
4. Release after a real drag, click a second time, or press Enter to
   commit: the offset runs once and the inner face and/or ring face
   appear as Part::Feature objects (one undoable transaction).
5. Esc at any point cancels cleanly: callbacks removed, document
   untouched (the offset never ran).

Mouse/keyboard plumbing is identical to PushPull's commands.py: a
dict-style "SoEvent" view callback for the mouse (the documented idiom
core Draft uses) and an application-level Qt event filter for the typed
path, because FreeCAD binds bare digit keys to standard-view shortcuts
that would otherwise swallow the input (see _KeyFilter).

Known v1 gap: no live rubber preview of the offset outline while
dragging. The status-bar readout shows the live distance; the offset
wire is computed once, at commit. See README "Known gaps".
"""
import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from .controller import OffsetController

_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Resources",
    "Icons",
)
_ICON_PATH = os.path.join(_ICON_DIR, "offsettool.svg")

_TYPE_KEYS = {
    QtCore.Qt.Key_0: "0", QtCore.Qt.Key_1: "1", QtCore.Qt.Key_2: "2",
    QtCore.Qt.Key_3: "3", QtCore.Qt.Key_4: "4", QtCore.Qt.Key_5: "5",
    QtCore.Qt.Key_6: "6", QtCore.Qt.Key_7: "7", QtCore.Qt.Key_8: "8",
    QtCore.Qt.Key_9: "9", QtCore.Qt.Key_Period: ".", QtCore.Qt.Key_Minus: "-",
}


class OffsetCommand(object):
    """Opens (and drives) a single click-drag(-type)-commit session."""

    def __init__(self):
        self._view = None
        self._sg_callback = None
        self._key_filter = None
        self.controller = None
        self._down_pos = None
        self._moved_since_arm = False

    def GetResources(self):
        return {
            "MenuText": "Offset",
            "ToolTip": (
                "Click a planar face and drag inward/outward to offset its "
                "boundary in its own plane -- or click, type an exact "
                "distance, and press Enter. Produces the inner face and/or "
                "the surrounding ring, ready for Push/Pull."
            ),
            "Pixmap": _ICON_PATH,
        }

    def IsActive(self):
        return App.ActiveDocument is not None and Gui.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        self._view = Gui.ActiveDocument.ActiveView
        self.controller = OffsetController(doc, view=self._view)
        self._down_pos = None
        self._moved_since_arm = False

        self._sg_callback = self._view.addEventCallback("SoEvent", self._on_event)
        self._install_key_filter()

        # Convenience entry point: a face selected the normal FreeCAD way
        # before invoking this command starts the session right away.
        # Default resolve (=1): SubElementNames come back as plain "FaceN"
        # strings and Object is the leaf feature (PushPull verified this
        # interactively; same API).
        sel = Gui.Selection.getSelectionEx()
        if len(sel) == 1 and len(sel[0].SubElementNames) == 1:
            ok, msg = self.controller.start(sel[0].Object, sel[0].SubElementNames[0])
            if not ok:
                self._status(msg)
        else:
            self._status("OffsetTool: click a planar face to start (Esc cancels).")

    # -- Qt keyboard handling --------------------------------------------
    def _install_key_filter(self):
        # Application-level, not widget-level: FreeCAD binds bare digit
        # keys to "set standard view" shortcuts, which otherwise win the
        # race and swallow every typed digit (found the hard way while
        # building PushPull's GUI driver; same dispatch, same fix).
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        self._key_filter = _KeyFilter(self)
        app.installEventFilter(self._key_filter)

    def _remove_key_filter(self):
        if self._key_filter is not None:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.removeEventFilter(self._key_filter)
            self._key_filter = None

    def wants_key(self, event):
        """True if this command would act on ``event``'s key right now;
        used to accept() ShortcutOverride so FreeCAD's digit-key view
        shortcuts do not eat the typed distance."""
        if self.controller is None or not self.controller.active:
            return False
        key = event.key()
        return key in _TYPE_KEYS or key in (
            QtCore.Qt.Key_Escape,
            QtCore.Qt.Key_Return,
            QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Backspace,
        )

    def handle_key(self, event):
        """Called by _KeyFilter for every QEvent.KeyPress while this
        command is active. Returns True if the event was consumed."""
        if self.controller is None or not self.controller.active:
            # Escape before a face is armed still ends the command.
            if event.key() == QtCore.Qt.Key_Escape and self.controller is not None:
                self.controller.cancel()
                self._teardown()
                return True
            return False
        key = event.key()
        if key == QtCore.Qt.Key_Escape:
            self.controller.cancel()
            self._teardown()
            return True
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.controller.key_return()
            self._teardown()
            return True
        if key == QtCore.Qt.Key_Backspace:
            self.controller.key_backspace()
            return True
        if key in _TYPE_KEYS:
            self.controller.type_char(_TYPE_KEYS[key])
            return True
        return False

    # -- SoEvent (mouse) handling ----------------------------------------
    def _on_event(self, arg):
        etype = arg.get("Type")
        if etype == "SoKeyboardEvent":
            if arg.get("Key") == "ESCAPE":
                self.controller.cancel()
                self._teardown()
            return
        if etype == "SoLocation2Event":
            self._on_mouse_move(arg)
            return
        if etype == "SoMouseButtonEvent":
            self._on_mouse_button(arg)
            return

    def _on_mouse_move(self, arg):
        pos = arg.get("Position")
        if pos is not None and self._down_pos is not None:
            dx = pos[0] - self._down_pos[0]
            dy = pos[1] - self._down_pos[1]
            if (dx * dx + dy * dy) ** 0.5 > OffsetController.DRAG_PIXEL_THRESHOLD:
                self._moved_since_arm = True

        if self.controller is None or not self.controller.active or pos is None:
            return
        point = self._plane_point(pos)
        if point is not None:
            self.controller.update_from_plane_point(point)

    def _on_mouse_button(self, arg):
        if arg.get("Button") != "BUTTON1":
            return
        state = arg.get("State")
        if state == "DOWN":
            if self.controller.active:
                # second click while armed -> commit at current distance
                self.controller.commit()
                self._teardown()
                return
            obj, sub = self._preselection_pick()
            if obj is None:
                self._status("OffsetTool: click a planar face to start (Esc cancels).")
                return
            ok, msg = self.controller.start(obj, sub)
            self._status(msg)
            if ok:
                self._down_pos = arg.get("Position")
                self._moved_since_arm = False
        elif state == "UP":
            if self.controller.active and self._moved_since_arm:
                self.controller.commit()
                self._teardown()

    # -- helpers ----------------------------------------------------------
    def _preselection_pick(self):
        try:
            presel = Gui.Selection.getPreselection()
        except Exception:
            presel = None
        if presel is None or not presel.SubElementNames:
            return None, None
        sub = presel.SubElementNames[0]
        obj = App.ActiveDocument.getObject(presel.Object.Name)
        return obj, sub

    def _pick_ray(self, pos):
        """Unproject a 2D screen position to a 3D pick ray (origin,
        direction) -- the same technique core Draft uses (WorkingPlane.
        getApparentPoint) and PushPull's command copies."""
        try:
            view = self._view
            pt = view.getPoint(pos[0], pos[1])
            if view.getCameraType() == "Perspective":
                camera = view.getCameraNode()
                p = camera.getField("position").getValue()
                cam_pos = App.Vector(p[0], p[1], p[2])
                ray_dir = pt.sub(cam_pos)
                return cam_pos, ray_dir
            else:
                ray_dir = view.getViewDirection()
                return pt, ray_dir
        except Exception:
            return None, None

    def _plane_point(self, pos):
        """Intersect the mouse pick ray with the armed face's plane."""
        if self.controller is None or self.controller.origin is None:
            return None
        ray_origin, ray_dir = self._pick_ray(pos)
        if ray_origin is None:
            return None
        n = self.controller.normal
        denom = ray_dir.dot(n)
        if abs(denom) < 1e-12:
            return None  # ray parallel to the face plane
        t = self.controller.origin.sub(ray_origin).dot(n) / denom
        if t < 0:
            return None
        return ray_origin.add(ray_dir.multiply(t))

    def _status(self, msg):
        try:
            Gui.getMainWindow().statusBar().showMessage(msg, 5000)
        except Exception:
            pass

    def _teardown(self):
        if self._view is not None and self._sg_callback is not None:
            try:
                self._view.removeEventCallback("SoEvent", self._sg_callback)
            except Exception:
                pass
            self._sg_callback = None
        self._remove_key_filter()
        self._down_pos = None
        self._moved_since_arm = False


class _KeyFilter(QtCore.QObject):
    """Thin, application-level Qt event filter forwarding keyboard input
    to the active OffsetCommand while a session is open.

    Handles two event types (same race PushPull's filter documents):
      - QEvent.ShortcutOverride: accept() it for keys we care about, so
        FreeCAD's built-in digit-key standard-view shortcuts do not
        consume the keypress before our KeyPress handler sees it.
      - QEvent.KeyPress: the actual typed-distance/commit/cancel logic.
    """

    def __init__(self, command):
        super().__init__()
        self._command = command

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QtCore.QEvent.ShortcutOverride:
            if self._command.wants_key(event):
                event.accept()
            return False
        if etype == QtCore.QEvent.KeyPress:
            if self._command.handle_key(event):
                return True
        return False


def register():
    Gui.addCommand("OffsetTool_Offset", OffsetCommand())
