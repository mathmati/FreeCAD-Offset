# FreeCAD OffsetTool

SketchUp's Offset tool for FreeCAD: click a planar face, drag inward or
outward (or type an exact distance), and the face's boundary offsets in
its own plane, producing the shrunken or grown face and the surrounding
ring as separate `Part::Feature` faces.

## Why

SketchUp has had this gesture for years: click a face, drag, and its
boundary moves inward or outward in the face's plane; the classic
follow-up is Push/Pull on the rim. FreeCAD's 2D offsets exist but live
in Draft (Draft Offset works on Draft objects) and in the Sketcher
(offsetting sketch geometry). Both operate on their own object types,
not on an arbitrary face you click in the 3D view. OffsetTool is the
face-first version, and it pairs with the companion PushPull addon:
offset the face here, then push/pull the rim or the inner face. Every
face this addon produces is planar and shaped exactly like what
PushPull accepts (a loose `Part::Feature` face), so the "offset, then
pull the rim" loop works end to end.

The offset itself is OCC's 2D wire offset with sharp (mitered) corners,
so exact dimensions survive: a 20x20 square offset by -2 is exactly
16x16, a radius-10 circle offset by -3 has area exactly pi*49.

## What it does (v1 scope)

1. Activate the **Offset** command (toolbar/menu, in its own
   workbench). If a single planar face is already selected the normal
   FreeCAD way, the session starts immediately; otherwise click a
   planar face in the 3D view. Any object's face works; a PartDesign
   Body is not required.
2. Move the mouse: the signed distance from the cursor to the face's
   boundary is tracked live in the status bar (inside the face is
   inward, outside is outward). Or type a number (digits, `.`, `-`)
   for an exact distance, negative meaning inward.
3. Release the drag, click a second time, or press Enter to commit.
   The offset runs once and the results appear as new `Part::Feature`
   objects:
   - Inward: `OffsetInner` (the shrunken face) and `OffsetRing` (the
     band between the original boundary and the inner one).
   - Outward: `OffsetRing` (the band around the still-existing source
     face).
   - The source face's object is never modified, and the placement is
     a single undoable transaction ("Offset face").
4. Esc at any point cancels cleanly: event callbacks are removed and
   the document is left untouched (the offset only ever runs at
   commit).

### Guards (friendly messages, not crashes)

- Non-planar face picked: "OffsetTool only supports planar faces (this
  one is curved)."
- Zero distance: "distance must be non-zero."
- Face with holes: declined with a v1-limit message (see Known gaps).
- Inward offset that collapses the face (distance at or beyond the
  inscribed radius): reported as a collapse with a suggestion to try a
  smaller distance. Nothing is created and the document is unchanged.
- Any other OCC offset failure: the message includes the kernel's own
  error text rather than a bare traceback.

If an inward offset pinches a narrow neck and splits the face into
several pieces, each piece is kept (placed as one compound
`OffsetInner`); nothing is silently dropped.

## Verification (what was and wasn't run)

Built and verified against a real, installed FreeCAD 1.1.1
(`freecadcmd`, bundled Python 3.11.14) on Windows:

- **Headless, run and green:** `verify/headless_regression.py` drives
  `core.offset_face` and the `OffsetController` state machine directly
  by method call ("the user typed -2.5 and pressed Enter" is
  `type_char('-'); ...; key_return()`). It covers exact square
  inward/outward areas (256 and 576 for a 20x20 face at distance 2),
  ring areas, the circle's pi*49, an L-shape with convex and concave
  corners, document placement and undo/redo, the typed-distance path,
  all four refusal paths, planarity of every produced face, and the
  outward/inward sign convention on a real solid's faces including a
  Reversed one. **16/16 checks pass**; the count is pinned in the
  script (`EXPECTED_CHECKS = 16`).
- **GUI driver, shipped UNVERIFIED:** `verify/drivers/offset_driver.py`
  invokes the real `OffsetCommand.Activated()` against a real 3D view,
  selects the face through the real `Gui.Selection` API, types the
  distance as synthetic `QKeyEvent`s, asserts the commit, and
  screenshots the result. I could not run it in the build environment
  (no display), so it carries an UNVERIFIED header and the Verification
  claims above do not rely on it. Run it on a desktop session:
  `freecad verify/drivers/offset_driver.py`.

One API note for anyone porting this: in FreeCAD 1.1.1 the 2D offset is
a Shape method (`face.makeOffset2D(distance, join=2, fill=False)`);
there is no `Part.makeOffset2D` module function, and `Part.Face` has no
`isPlanar()` method, so planarity is checked via the Surface type (the
same idiom PushPull uses).

## Requirements

FreeCAD 1.1 or later. No third-party Python dependencies beyond what
FreeCAD itself ships (`PySide`, `pivy`).

## Known gaps (disclosed up front)

- Faces with holes are refused. The underlying OCC 2D offset errors on
  them in 1.1.1, so v1 declines them up front with a message instead of
  failing mid-commit.
- No live rubber preview of the offset outline while dragging. The
  status bar shows the live distance and the offset wire is computed
  once, at commit. Numeric correctness was prioritised over the
  preview.
- Only one face per command activation; re-invoke the command to offset
  another face.
- The typed distance offsets the whole boundary; per-edge offsets are
  out of scope.
- Not internationalized (UI strings are plain Python, not
  `QT_TRANSLATE_NOOP`-wrapped).

## License

MIT, see `LICENSE`.

## Transparency

Built with AI assistance (Kimi Code CLI); reviewed and posted by a
human.
