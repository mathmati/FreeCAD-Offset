# SPDX-License-Identifier: MIT
"""OffsetController: the click-drag(-type)-commit state machine.

Deliberately decoupled from raw Coin/pivy/Qt event objects so it can be
driven two ways with identical code paths (the same pattern the PushPull
addon uses for its PushPullController):

  1. From commands.py's real SoEvent/Qt callbacks in the live GUI.
  2. Directly, by method call, from a headless (freecadcmd) test or a
     GUI-driven verification script -- simulating "the user typed 2.5 and
     pressed Enter" as ``type_char('2'); type_char('.'); type_char('5');
     key_return()`` and "the cursor sits at this point of the face's
     plane" as ``update_from_plane_point(p)``.

The offset itself (an OCC call) happens exactly once, at commit. Mouse
ticks only update a float and a status-bar readout.
"""
import Part

from . import core


class OffsetController(object):
    """One instance per in-progress (or just-finished) offset session.

    ``view`` is the live ``Gui.ActiveDocument.ActiveView`` when driven
    from the real GUI, or None for headless use (pure state-machine
    bookkeeping, no status-bar text).
    """

    #: pixel movement below which a mouse-up is "just a click to arm",
    #: not the end of a drag (used by commands.py, kept here so the GUI
    #: and the docs quote one number).
    DRAG_PIXEL_THRESHOLD = 4

    #: |distance| below this is a no-op commit: rejected with a message,
    #: nothing created. Matches PushPull's MIN_LENGTH guard.
    MIN_COMMIT = 1e-3

    def __init__(self, doc, view=None):
        self.doc = doc
        self.view = view
        self.reset()

    def reset(self):
        self.active = False
        self.source_obj = None
        self.face_name = None
        self.face = None
        self.origin = None
        self.normal = None
        self.distance = 0.0
        self.typed_buffer = ""
        self.committed_objects = []
        self.last_message = ""

    # -- pick / start --------------------------------------------------
    def start(self, obj, sub_name):
        """Validate and begin a session from a (obj, sub_element_name)
        pick, e.g. straight from Gui.Selection or preselection.

        Returns (True, "ok") on success, or (False, message) on a
        friendly rejection (non-face pick, non-planar face, face with
        holes); the caller shows ``message`` and does not enter drag
        mode. Unlike PushPull there is no Body requirement: any planar
        face of any object can be offset.
        """
        self.reset()
        if obj is None:
            self.last_message = "OffsetTool: nothing selected."
            return False, self.last_message
        if not sub_name or not sub_name.startswith("Face"):
            self.last_message = "OffsetTool: select a face, not an edge or vertex."
            return False, self.last_message
        try:
            face = obj.getSubObject(sub_name)
        except Exception:
            face = None
        if face is None or not isinstance(face, Part.Face):
            self.last_message = "OffsetTool: could not resolve that face."
            return False, self.last_message
        try:
            core.validate_face(face)
        except core.OffsetError as exc:
            self.last_message = str(exc)
            return False, self.last_message

        self.source_obj = obj
        self.face_name = sub_name
        self.face = face
        self.origin = face.CenterOfMass
        self.normal = core.face_normal(face)
        self.active = True
        self.distance = 0.0
        self.typed_buffer = ""
        self._update_readout()
        return True, "ok"

    # -- live drag -------------------------------------------------------
    def update_distance(self, distance):
        """Set the live offset distance (mm, signed: positive outward).
        Cheap: stores a float and updates the status-bar text, no OCC
        call (the offset runs once, at commit)."""
        if not self.active:
            return self.distance
        self.distance = float(distance)
        self._update_readout()
        return self.distance

    def update_from_plane_point(self, point):
        """SketchUp-style drag distance from a point in the face's plane
        (e.g. the mouse ray intersected with the plane): the signed
        distance from the point to the face's boundary, negative when the
        point lies inside the face (dragging inward), positive when it
        lies outside."""
        if not self.active or self.face is None:
            return self.distance
        dist = self.face.OuterWire.distToShape(Part.Vertex(point))[0]
        if self.face.isInside(point, 1e-6, True):
            dist = -dist
        return self.update_distance(dist)

    # -- typed path ------------------------------------------------------
    def type_char(self, ch):
        """Feed one typed character (digit, '.', '-') into the buffer and
        live-update the distance when the buffer parses as a float."""
        if not self.active:
            return
        if ch not in "0123456789.-":
            return
        self.typed_buffer += ch
        self._parse_buffer()
        self._update_readout()

    def key_backspace(self):
        if not self.active or not self.typed_buffer:
            return
        self.typed_buffer = self.typed_buffer[:-1]
        self._parse_buffer()
        self._update_readout()

    def _parse_buffer(self):
        try:
            self.distance = float(self.typed_buffer)
        except ValueError:
            pass  # "-", "", "1.2.3": keep the last good distance

    # -- commit / cancel -------------------------------------------------
    def key_return(self):
        """Enter pressed: commit at the current distance."""
        return self.commit()

    def commit(self):
        """Run the one OCC offset and place the result faces in the
        document as one undoable transaction. Returns the list of created
        objects, or None (with ``last_message`` set) on a friendly
        rejection; the session stays active then so the user can adjust
        the distance."""
        if not self.active:
            return None
        if abs(self.distance) < self.MIN_COMMIT:
            self.last_message = (
                "OffsetTool: distance too small, nothing to commit.")
            return None
        try:
            _result, created = core.commit_offset(self.doc, self.face, self.distance)
        except core.OffsetError as exc:
            self.last_message = str(exc)
            return None
        self.committed_objects = created
        self.active = False
        names = ", ".join(o.Name for o in created)
        self.last_message = "OffsetTool: created %s." % names
        self._update_readout()
        return created

    def cancel(self):
        """Esc: end the session. The document was never touched (the
        offset only runs at commit), so there is nothing to clean up
        beyond bookkeeping."""
        self.active = False
        self.committed_objects = []
        self.last_message = "OffsetTool: cancelled."

    # -- readout ---------------------------------------------------------
    def _update_readout(self):
        if self.view is None:
            return
        try:
            import FreeCADGui as Gui
            Gui.getMainWindow().statusBar().showMessage(self.readout_text(), 5000)
        except Exception:
            pass

    def readout_text(self):
        """The status-bar line for the current state (also used by the
        GUI verification driver, which matches on it)."""
        if self.typed_buffer:
            value = self.typed_buffer
        else:
            value = "%g" % self.distance
        direction = "outward" if self.distance >= 0 else "inward"
        return ("OffsetTool: Offset %s mm %s (Enter=commit, Esc=cancel)"
                % (value, direction))
