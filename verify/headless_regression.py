# SPDX-License-Identifier: MIT
"""verify/headless_regression.py -- OffsetTool headless regression (freecadcmd).

Run from the repo root (print() under freecadcmd is unreliable; use the
stdout-redirect wrapper from the README):

    freecadcmd verify/headless_regression.py

Exit code 0 and a final "16/16 checks pass" line when green.

What this is: the script the OffsetTool README's "Verification" section
describes. It drives ``freecad.OffsetToolWB.core`` (the geometry) and
``freecad.OffsetToolWB.controller.OffsetController`` (the click-drag(-type)
-commit state machine) directly by method call: "the user typed -2.5 and
pressed Enter" is ``type_char('-'); type_char('2'); type_char('.');
type_char('5'); key_return()``.

The 16 checks, in order (geometry checks run on plain Part shapes; the
document checks share one document on purpose, reading as one continuous
modeling session):

   setup           1      loose 20x20 Part::Feature face valid + planar
   square inward   2-4    -2 -> inner exactly 16x16 (area 256), ring 144,
                          both planar, normals follow the source face
   square outward  5      +2 -> outer exactly 24x24 (area 576), ring 176
   circle          6      r=10 offset -3 -> inner area exactly pi*49
   L-shape         7      -1 -> inner area within 1% of the analytic 224
   placement       8-9    OffsetInner/OffsetRing appear as Part::Feature,
                          source object untouched, undo/redo works
   controller      10-11  typed "-2.5" + Enter commits exact 15x15 inner;
                          plane-point drag distance, cancel leaves doc clean
   refusals        12-15  non-planar face / zero distance / face with
                          holes / collapsing inward offset
   planar output   16     every produced face passes core.is_planar, and
                          offsets of a real solid's faces (incl. a
                          Reversed one) keep the exact-area sign convention
"""
import math
import os
import sys
import traceback

# --- make the workbench importable from a source checkout ------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
try:
    import freecad  # FreeCAD's own namespace package (present under freecadcmd)
    freecad.__path__ = [os.path.join(_REPO_ROOT, "freecad")] + list(freecad.__path__)
except ImportError:  # extremely defensive: fall back to plain sys.path
    sys.path.insert(0, _REPO_ROOT)

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from freecad.OffsetToolWB import core  # noqa: E402
from freecad.OffsetToolWB.controller import OffsetController  # noqa: E402

EXPECTED_CHECKS = 16

_checks = []


def check(name):
    def deco(fn):
        _checks.append((name, fn))
        return fn
    return deco


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def approx(a, b, tol, msg):
    a = getattr(a, "Value", a)  # Quantity -> float
    b = getattr(b, "Value", b)
    if abs(a - b) > tol:
        raise AssertionError("%s (got %r, want %r +/- %r)" % (msg, a, b, tol))


def square_wire(size=20.0):
    v = [App.Vector(0, 0, 0), App.Vector(size, 0, 0),
         App.Vector(size, size, 0), App.Vector(0, size, 0)]
    return Part.makePolygon(v + [v[0]])


def object_names(doc):
    return sorted(o.Name for o in doc.Objects)


# --- shared fixture ---------------------------------------------------------
class Fixture(object):
    """One document with one loose 20x20 planar face object, plus the pure
    shapes the geometry checks need."""

    def __init__(self):
        self.doc = App.newDocument("OffsetVerify")
        self.doc.UndoMode = 1  # console docs default to 0; undo check needs 1
        self.src = self.doc.addObject("Part::Feature", "SourceFace")
        self.src.Shape = Part.Face(square_wire(20.0))
        self.doc.recompute()
        self.face = self.src.getSubObject("Face1")


# --- 1: setup ----------------------------------------------------------------
@check("setup: loose 20x20 face object is valid, planar, area 400")
def c01(fx):
    ok(fx.src.Shape.isValid(), "source face shape is invalid")
    ok(core.is_planar(fx.face), "source face is not planar")
    approx(fx.face.Area, 400.0, 1e-9, "source face area")


# --- 2-4: square inward --------------------------------------------------------
@check("inward: offset_face(face, -2) returns an inner face and a ring")
def c02(fx):
    fx.res_in = core.offset_face(fx.face, -2.0)
    ok(fx.res_in.inner_face is not None, "no inner face produced")
    ok(fx.res_in.ring_face is not None, "no ring face produced")
    ok(len(fx.res_in.offset_wires) >= 1, "no offset wires produced")


@check("inward: inner is exactly 16x16 (area 256), normal follows the source")
def c03(fx):
    inner = fx.res_in.inner_face
    approx(inner.Area, 256.0, 1e-9, "inner face area")
    bb = inner.BoundBox
    approx(bb.XMin, 2.0, 1e-9, "inner XMin")
    approx(bb.YMin, 2.0, 1e-9, "inner YMin")
    approx(bb.XMax, 18.0, 1e-9, "inner XMax")
    approx(bb.YMax, 18.0, 1e-9, "inner YMax")
    approx(core.face_normal(inner).z, 1.0, 1e-9, "inner normal z")


@check("inward: ring area is exactly 400-256 = 144, planar, two wires")
def c04(fx):
    ring = fx.res_in.ring_face
    approx(ring.Area, 144.0, 1e-9, "ring face area")
    ok(core.is_planar(ring), "ring is not planar")
    ok(len(ring.Wires) == 2, "ring should have 2 wires, has %d" % len(ring.Wires))
    approx(core.face_normal(ring).z, 1.0, 1e-9, "ring normal z")


# --- 5: square outward ---------------------------------------------------------
@check("outward: +2 gives an exactly 24x24 outer face and a 176 ring band")
def c05(fx):
    res = core.offset_face(fx.face, 2.0)
    outer = res.outer_face
    ok(outer is not None, "no outer face produced")
    approx(outer.Area, 576.0, 1e-9, "outer face area")
    bb = outer.BoundBox
    approx(bb.XMin, -2.0, 1e-9, "outer XMin")
    approx(bb.XMax, 22.0, 1e-9, "outer XMax")
    ok(res.ring_face is not None, "no outward ring band produced")
    approx(res.ring_face.Area, 176.0, 1e-9, "outward ring band area")
    ok(core.is_planar(res.ring_face), "outward ring is not planar")


# --- 6: circle ------------------------------------------------------------------
@check("circle: r=10 offset -3 gives inner area exactly pi*49 (circle kept)")
def c06(fx):
    circ = Part.Face(Part.Wire([Part.makeCircle(10.0)]))
    res = core.offset_face(circ, -3.0)
    inner = res.inner_face
    ok(inner is not None, "no inner face produced for the circle")
    approx(inner.Area, math.pi * 49.0, 1e-9, "circle inner area")
    curve = inner.OuterWire.Edges[0].Curve
    ok(isinstance(curve, Part.Circle), "offset curve is %s, not a Circle"
       % type(curve).__name__)
    approx(curve.Radius, 7.0, 1e-9, "offset circle radius")
    approx(res.ring_face.Area, math.pi * (100.0 - 49.0), 1e-9, "circle ring area")


# --- 7: L-shape -------------------------------------------------------------------
@check("L-shape: -1 keeps planarity and the analytic area (224) within 1%")
def c07(fx):
    # 20x20 square minus a 10x10 notch at top-right, CCW. Inward miter
    # offset by 1: strips shrink to 18x8 and 8x10 -> 144 + 80 = 224.
    pts = [App.Vector(0, 0, 0), App.Vector(20, 0, 0), App.Vector(20, 10, 0),
           App.Vector(10, 10, 0), App.Vector(10, 20, 0), App.Vector(0, 20, 0)]
    lface = Part.Face(Part.makePolygon(pts + [pts[0]]))
    approx(lface.Area, 300.0, 1e-9, "L source area")
    res = core.offset_face(lface, -1.0)
    inner = res.inner_face
    ok(inner is not None, "no inner face produced for the L")
    ok(len(res.inner_faces) == 1, "L offset should be one piece, got %d"
       % len(res.inner_faces))
    ok(core.is_planar(inner), "L inner face is not planar")
    ok(abs(inner.Area - 224.0) <= 224.0 * 0.01,
       "L inner area %r not within 1%% of 224" % inner.Area)
    ok(res.ring_face is not None and core.is_planar(res.ring_face),
       "L ring missing or not planar")


# --- 8-9: document placement + undo ------------------------------------------------
@check("placement: commit_offset adds OffsetInner/OffsetRing, source untouched")
def c08(fx):
    src_area_before = fx.src.Shape.Area
    src_verts_before = len(fx.src.Shape.Vertexes)
    names_before = object_names(fx.doc)
    fx.place_result, fx.placed = core.commit_offset(fx.doc, fx.face, -2.0)
    names = [o.Name for o in fx.placed]
    ok(any(n.startswith("OffsetInner") for n in names),
       "no OffsetInner* object placed: %r" % names)
    ok(any(n.startswith("OffsetRing") for n in names),
       "no OffsetRing* object placed: %r" % names)
    for o in fx.placed:
        ok(o.TypeId == "Part::Feature", "%s is %s" % (o.Name, o.TypeId))
        ok(len(o.Shape.Solids) == 0, "%s unexpectedly contains a solid" % o.Name)
        ok(all(core.is_planar(f) for f in o.Shape.Faces),
           "%s has a non-planar face (not PushPull-ready)" % o.Name)
    # source object not modified
    approx(fx.src.Shape.Area, src_area_before, 1e-9, "source object area changed")
    ok(len(fx.src.Shape.Vertexes) == src_verts_before,
       "source object topology changed")
    ok(object_names(fx.doc) == sorted(names_before + names),
       "unexpected object set change")


@check("placement: the transaction undoes and redoes cleanly")
def c09(fx):
    placed_names = sorted(o.Name for o in fx.placed)
    fx.doc.undo()
    ok(all(fx.doc.getObject(n) is None for n in placed_names),
       "undo did not remove the placed faces")
    fx.doc.redo()
    ok(all(fx.doc.getObject(n) is not None for n in placed_names),
       "redo did not restore the placed faces")


# --- 10-11: controller typed + drag paths --------------------------------------------
@check("typed path: '-2.5' + Enter commits an exact 15x15 OffsetInner")
def c10(fx):
    ctl = OffsetController(fx.doc)  # view=None -> headless bookkeeping only
    started, msg = ctl.start(fx.src, "Face1")
    ok(started, "start() rejected the loose face: %s" % msg)
    for ch in "-2.5":
        ctl.type_char(ch)
    ok(ctl.typed_buffer == "-2.5", "typed_buffer is %r" % ctl.typed_buffer)
    approx(ctl.distance, -2.5, 1e-9, "live preview of typed distance")
    created = ctl.key_return()
    ok(created is not None, "typed commit failed: %s" % ctl.last_message)
    inner = [o for o in created if o.Name.startswith("OffsetInner")]
    ok(len(inner) == 1, "expected one OffsetInner, got %r" % [o.Name for o in created])
    approx(inner[0].Shape.Area, 225.0, 1e-9, "typed-path inner area (15x15)")
    ok(all(core.is_planar(f) for f in inner[0].Shape.Faces),
       "typed-path inner is not planar")


@check("drag path: plane point gives signed distance; cancel leaves doc clean")
def c11(fx):
    names_before = object_names(fx.doc)
    ctl = OffsetController(fx.doc)
    started, msg = ctl.start(fx.src, "Face1")
    ok(started, "start() rejected the loose face: %s" % msg)
    # (3,10,0) is inside the 20x20 face, 3 mm from the nearest edge.
    ctl.update_from_plane_point(App.Vector(3, 10, 0))
    approx(ctl.distance, -3.0, 1e-9, "drag distance from an interior point")
    # (25,10,0) is outside, 5 mm from the right edge: outward.
    ctl.update_from_plane_point(App.Vector(25, 10, 0))
    approx(ctl.distance, 5.0, 1e-9, "drag distance from an exterior point")
    ctl.cancel()
    ok(not ctl.active, "controller still active after cancel()")
    ok(ctl.committed_objects == [], "cancel() produced committed objects")
    ok(object_names(fx.doc) == names_before, "document changed after cancel()")


# --- 12-15: refusals ------------------------------------------------------------------
@check("refusal: a non-planar face gets the 'planar faces' message")
def c12(fx):
    cyl = Part.makeCylinder(5, 10)
    curved = None
    for f in cyl.Faces:
        if not core.is_planar(f):
            curved = f
            break
    ok(curved is not None, "could not find a curved face on the cylinder")
    try:
        core.offset_face(curved, -1.0)
    except core.OffsetError as exc:
        ok("planar" in str(exc).lower(), "unexpected message: %s" % exc)
    else:
        raise AssertionError("curved face was accepted")


@check("refusal: a zero distance is rejected")
def c13(fx):
    try:
        core.offset_face(fx.face, 0.0)
    except core.OffsetError as exc:
        ok("non-zero" in str(exc).lower(), "unexpected message: %s" % exc)
    else:
        raise AssertionError("zero distance was accepted")


@check("refusal: a face with holes gets the v1-limit message")
def c14(fx):
    hole = Part.makePolygon([App.Vector(7, 7, 0), App.Vector(12, 7, 0),
                             App.Vector(12, 12, 0), App.Vector(7, 12, 0),
                             App.Vector(7, 7, 0)])
    holed = fx.face.cut(Part.Face(hole)).Faces[0]
    ok(len(holed.Wires) == 2, "fixture: holed face should have 2 wires")
    try:
        core.offset_face(holed, -1.0)
    except core.OffsetError as exc:
        ok("holes" in str(exc).lower(), "unexpected message: %s" % exc)
    else:
        raise AssertionError("face with holes was accepted")


@check("refusal: an inward offset that collapses the face is reported, doc clean")
def c15(fx):
    names_before = object_names(fx.doc)
    try:
        core.offset_face(fx.face, -25.0)
    except core.OffsetError as exc:
        ok("collapse" in str(exc).lower(), "unexpected message: %s" % exc)
    else:
        raise AssertionError("collapsing offset was accepted")
    ok(object_names(fx.doc) == names_before,
       "document changed by a refused offset")


# --- 16: produced faces planar + real-solid sign convention ------------------------
@check("planar output: all products pass is_planar; solid faces keep exact sign")
def c16(fx):
    res = core.offset_face(fx.face, -2.0)
    produced = [res.inner_face, res.ring_face]
    produced.append(core.offset_face(fx.face, 2.0).outer_face)
    produced.append(core.offset_face(
        Part.Face(Part.Wire([Part.makeCircle(10.0)])), -3.0).inner_face)
    for f in produced:
        ok(f is not None and core.is_planar(f), "a produced face is not planar")
    # A real solid's faces: top (Forward) and bottom (Reversed). Positive
    # must mean outward relative to that face in both cases.
    box = Part.makeBox(20, 20, 10)
    top = [f for f in box.Faces if core.is_planar(f)
           and f.CenterOfMass.z > 9.9 and f.Area > 399][0]
    bot = [f for f in box.Faces if core.is_planar(f)
           and f.CenterOfMass.z < 0.1 and f.Area > 399][0]
    ok(bot.Orientation == "Reversed", "fixture: expected a Reversed bottom face")
    approx(core.offset_face(top, -2.0).inner_face.Area, 256.0, 1e-9,
           "box top inward area")
    approx(core.offset_face(bot, -2.0).inner_face.Area, 256.0, 1e-9,
           "box bottom (Reversed) inward area")
    approx(core.offset_face(bot, 2.0).outer_face.Area, 576.0, 1e-9,
           "box bottom (Reversed) outward area")


def main():
    fx = Fixture()
    passed = 0
    failures = []
    for idx, (name, fn) in enumerate(_checks, 1):
        try:
            fn(fx)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures.append((idx, name, exc))
            print("[FAIL %2d] %s" % (idx, name))
            traceback.print_exc()
        else:
            passed += 1
            print("[ ok  %2d] %s" % (idx, name))
    total = passed + len(failures)
    print("-" * 64)
    print("%d/%d checks pass" % (passed, total))
    if total != EXPECTED_CHECKS:
        print("WARNING: ran %d checks, expected %d -- update EXPECTED_CHECKS"
              % (total, EXPECTED_CHECKS))
    if failures:
        print("FAILURES:")
        for idx, name, exc in failures:
            print("  %2d. %s: %s" % (idx, name, exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
