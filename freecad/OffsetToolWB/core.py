# SPDX-License-Identifier: MIT
"""App-side 2D offset of a planar face (the geometry heart of OffsetTool).

Everything here works on plain Part shapes and document objects: no Coin,
no Gui.Selection, no PySide. Importable and testable under plain
``freecadcmd``.

Probed facts (FreeCAD 1.1.1, see the build-session probe scripts):

  * ``Part.makeOffset2D`` does NOT exist as a module function in 1.1.1; the
    entry point is the Shape method ``face.makeOffset2D(offset, join=0,
    fill=False, openResult=False, intersection=False)`` (BRepOffsetAPI_
    MakeOffset semantics: positive expands, negative shrinks).
  * Called on a Face (not a bare wire) the sign is correct even for
    ``Orientation == 'Reversed'`` faces: OCC offsets in the face's own
    frame, so positive is outward relative to that face, always.
  * ``join=2`` (intersection/miter) keeps corners sharp: a 20x20 square
    offset by -2 is exactly 16x16 (area 256.0); ``join=0`` (arcs) rounds
    corners and breaks exact area math.
  * Offsetting a circle keeps an exact Circle curve (r=10 offset -3 gives
    area pi*49 to 1e-9).
  * An inward offset that collapses the face raises
    ``Base.CADKernelError: makeOffset2D: offset result has no wires.`` --
    a clean, detectable signal (no garbage geometry is returned).
  * A narrow neck that pinches off returns a Compound of several Faces
    (no exception); we support that and build one inner face per piece.
  * ``Part.Face`` in 1.1.1 has NO ``isPlanar()`` method; the planarity
    check used throughout is ``isinstance(face.Surface, Part.Plane)``
    (the same idiom the PushPull addon uses), with ``findPlane()`` as
    fallback.
"""
import FreeCAD as App
import Part

#: OCC join type for offsetting non-tangent joints. 0 = arcs (rounds
#: corners, breaks exact area math), 1 = tangent, 2 = intersection
#: (sharp miters). OffsetTool ships join=2: SketchUp offsets keep sharp
#: corners, and so do we.
JOIN_MITER = 2

#: |distance| below this is "zero distance" and refused.
MIN_DISTANCE = 1e-9

#: Faces with an area below this are treated as collapsed slivers and
#: dropped from results.
MIN_FACE_AREA = 1e-7

#: Message fragments OCC (Base.CADKernelError / ValueError) produces when
#: an inward offset eats the whole face, probed on 1.1.1.
_COLLAPSE_MARKERS = ("no wires", "no faces", "is null")


class OffsetError(Exception):
    """Friendly, user-facing message for why an offset cannot happen."""


class OffsetResult(object):
    """What :func:`offset_face` returns.

    Attributes:

    ``source_face``   the Part.Face that was offset (never modified).
    ``distance``      the signed distance that was applied.
    ``offset_shape``  the raw OCC result (a Face, or a Compound of Faces
                      when a narrow neck pinched the offset into pieces).
    ``offset_faces``  list of faces built on the offset boundary: the
                      shrunken inner face(s) for distance < 0, the grown
                      outer face for distance > 0.
    ``ring_faces``    list of faces for the surrounding band: source
                      minus inner (distance < 0), outer minus source
                      (distance > 0).

    Convenience singulars: ``offset_wires``, ``inner_face``,
    ``outer_face``, ``ring_face`` (first of each list, or None).
    """

    def __init__(self, source_face, distance, offset_shape, offset_faces, ring_faces):
        self.source_face = source_face
        self.distance = distance
        self.offset_shape = offset_shape
        self.offset_faces = offset_faces
        self.ring_faces = ring_faces

    @property
    def offset_wires(self):
        wires = []
        for f in self.offset_faces:
            wires.extend(f.Wires)
        return wires

    @property
    def inner_face(self):
        return self.offset_faces[0] if self.distance < 0 and self.offset_faces else None

    @property
    def inner_faces(self):
        return self.offset_faces if self.distance < 0 else []

    @property
    def outer_face(self):
        return self.offset_faces[0] if self.distance > 0 and self.offset_faces else None

    @property
    def ring_face(self):
        return self.ring_faces[0] if self.ring_faces else None


def is_planar(face):
    """True if a Part.Face lies in a plane.

    1.1.1's Part.Face has no ``isPlanar()`` method, so this is the
    Surface-type check PushPull's face_utils uses, with ``findPlane()``
    as fallback for faces whose surface is a plane in disguise.
    """
    try:
        if isinstance(face.Surface, Part.Plane):
            return True
    except Exception:
        return False
    try:
        return face.findPlane() is not None
    except Exception:
        return False


def face_normal(face):
    """Unit normal of a planar face, corrected for the face's own
    Orientation flag (a Reversed face's geometric normalAt points the
    other way). Same gotcha PushPull's face_utils documents."""
    normal = face.normalAt(0, 0)
    if face.Orientation == "Reversed":
        normal = normal.multiply(-1)
    normal.normalize()
    return normal


def validate_face(face):
    """Raise OffsetError with a friendly message unless ``face`` is a
    single-wired planar Part.Face we can offset."""
    if not isinstance(face, Part.Face):
        raise OffsetError("OffsetTool: select a face, not an edge or vertex.")
    if not is_planar(face):
        raise OffsetError(
            "OffsetTool only supports planar faces (this one is curved).")
    if len(face.Wires) > 1:
        raise OffsetError(
            "OffsetTool does not support faces with holes yet (v1 limit).")


def _contained(inner_face, source_face, samples=12):
    """True if sampled points of ``inner_face``'s outer wire all lie
    strictly inside ``source_face``. A defensive check that an inward
    offset really shrank into the source instead of flipping outside it
    on some pathological input."""
    pts = inner_face.OuterWire.discretize(Number=samples + 1)
    return all(source_face.isInside(p, 1e-6, True) for p in pts)


def offset_face(face, distance):
    """Offset a planar face's boundary in its own plane.

    ``distance`` > 0 offsets outward (the face grows), < 0 inward (the
    face shrinks). Returns an OffsetResult with the offset wire(s), the
    inner face (distance < 0, when it survives) and the ring face (source
    minus inner, or outer minus source). The source face is never
    modified.

    Raises OffsetError with a friendly message for: a non-planar face, a
    face with holes (v1 limit), a zero distance, an inward offset that
    collapses the face, and any other OCC offset failure (the kernel's
    own error text is included).
    """
    distance = float(distance)
    if abs(distance) < MIN_DISTANCE:
        raise OffsetError("OffsetTool: distance must be non-zero.")
    validate_face(face)

    try:
        raw = face.makeOffset2D(distance, JOIN_MITER, False)
    except Exception as exc:
        text = str(exc)
        if any(marker in text for marker in _COLLAPSE_MARKERS):
            raise OffsetError(
                "OffsetTool: an inward offset of %g mm collapses the face "
                "(nothing of it survives); try a smaller distance." % abs(distance))
        raise OffsetError("OffsetTool: OCC offset failed: %s" % text)

    offset_faces = [f for f in raw.Faces if f.Area > MIN_FACE_AREA]
    if not offset_faces:
        # Defensive: 1.1.1 raises on collapse (caught above), but if a
        # future kernel ever returns an empty/degenerate result instead,
        # report the same friendly message rather than placing garbage.
        raise OffsetError(
            "OffsetTool: an inward offset of %g mm collapses the face "
            "(nothing of it survives); try a smaller distance." % abs(distance))

    if distance < 0:
        for inner in offset_faces:
            if not _contained(inner, face):
                raise OffsetError(
                    "OffsetTool: an inward offset of %g mm collapses the "
                    "face (nothing of it survives); try a smaller distance."
                    % abs(distance))
        ring = face.cut(raw)
    else:
        ring = raw.cut(face)

    ring_faces = [f for f in ring.Faces if f.Area > MIN_FACE_AREA]
    return OffsetResult(face, distance, raw, offset_faces, ring_faces)


def _as_shape(faces):
    """One face -> the face itself; several -> a Compound of them."""
    if len(faces) == 1:
        return faces[0]
    return Part.makeCompound(faces)


def place_result(doc, result):
    """Add an OffsetResult's faces to the document as Part::Feature
    objects. Inward offsets place ``OffsetInner`` (the shrunken face) and
    ``OffsetRing`` (the surrounding band); outward offsets place
    ``OffsetRing`` (the band around the still-existing source face).
    Returns the list of created objects. The source face's own object is
    never touched.

    Opens no transaction itself; callers wanting undoability wrap this in
    ``doc.openTransaction(...)``/``commitTransaction()`` (see
    :func:`commit_offset`).
    """
    created = []
    if result.distance < 0 and result.inner_faces:
        inner_obj = doc.addObject("Part::Feature", "OffsetInner")
        inner_obj.Shape = _as_shape(result.inner_faces)
        created.append(inner_obj)
    if result.ring_faces:
        ring_obj = doc.addObject("Part::Feature", "OffsetRing")
        ring_obj.Shape = _as_shape(result.ring_faces)
        created.append(ring_obj)
    return created


def commit_offset(doc, face, distance):
    """offset_face + place_result as one undoable transaction.

    Returns (result, created_objects). On an OffsetError nothing is
    created and the document is left exactly as it was.
    """
    result = offset_face(face, distance)  # raises before anything is created
    doc.openTransaction("Offset face")
    try:
        created = place_result(doc, result)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    doc.recompute()
    return result, created
