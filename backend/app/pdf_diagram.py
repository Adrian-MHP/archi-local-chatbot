from __future__ import annotations

from typing import Any, Dict, List, Tuple

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

# Geometry thresholds (PDF points).
MIN_BOX_WIDTH = 30.0
MIN_BOX_HEIGHT = 15.0
BOX_DEDUPE_TOLERANCE = 3.0
ARROWHEAD_MAX_SIZE = 20.0
MIN_CONNECTOR_LENGTH = 15.0
MAX_ENDPOINT_TO_BOX_DISTANCE = 40.0
MAX_ARROWHEAD_TO_ENDPOINT_DISTANCE = 15.0
MAX_PAGES = 6
# A rect spanning nearly the full page width or height is a structural element -- a swimlane/
# table-row band or the outer table border -- never a real task box. Real content boxes are
# always substantially narrower/shorter than the page they're drawn on.
CONTAINER_SPAN_FRACTION = 0.85


def pdf_diagram_support_available() -> bool:
    return fitz is not None


def _rects_close(a: "fitz.Rect", b: "fitz.Rect", tolerance: float = BOX_DEDUPE_TOLERANCE) -> bool:
    return (
        abs(a.x0 - b.x0) <= tolerance
        and abs(a.y0 - b.y0) <= tolerance
        and abs(a.x1 - b.x1) <= tolerance
        and abs(a.y1 - b.y1) <= tolerance
    )


def _dist(p: "fitz.Point", q: "fitz.Point") -> float:
    return ((p.x - q.x) ** 2 + (p.y - q.y) ** 2) ** 0.5


def _point_to_rect_distance(point: "fitz.Point", rect: "fitz.Rect") -> float:
    cx = min(max(point.x, rect.x0), rect.x1)
    cy = min(max(point.y, rect.y0), rect.y1)
    return ((point.x - cx) ** 2 + (point.y - cy) ** 2) ** 0.5


def _item_start(item: Tuple[Any, ...]) -> "fitz.Point | None":
    if item[0] in ("l", "c") and isinstance(item[1], fitz.Point):
        return item[1]
    return None


def _item_end(item: Tuple[Any, ...]) -> "fitz.Point | None":
    if item[0] in ("l", "c") and isinstance(item[-1], fitz.Point):
        return item[-1]
    return None


def _path_is_closed(items: List[Tuple[Any, ...]], tolerance: float = 2.5) -> bool:
    if not items:
        return False
    start = _item_start(items[0])
    end = _item_end(items[-1])
    if start is None or end is None:
        return False
    return _dist(start, end) <= tolerance


def _is_box_drawing(drawing: Dict[str, Any]) -> "fitz.Rect | None":
    """A box is either a single axis-aligned rectangle, or a closed loop of line/curve
    segments (e.g. a rounded-corner rectangle: 4 straight edges + 4 bezier corners, the
    common style professional diagramming tools use for task boxes)."""
    items = drawing.get("items", [])
    if not items:
        return None

    if len(items) == 1 and items[0][0] == "re":
        rect = fitz.Rect(items[0][1])
    elif all(item[0] in ("l", "c") for item in items) and _path_is_closed(items):
        rect_attr = drawing.get("rect")
        if not rect_attr:
            return None
        rect = fitz.Rect(rect_attr)
    else:
        return None

    if rect.width >= MIN_BOX_WIDTH and rect.height >= MIN_BOX_HEIGHT:
        return rect
    return None


def _extract_box_rects(
    drawings: List[Dict[str, Any]], page_width: float | None = None, page_height: float | None = None
) -> List["fitz.Rect"]:
    candidates: List["fitz.Rect"] = []
    for drawing in drawings:
        rect = _is_box_drawing(drawing)
        if rect is None:
            continue
        if page_width and rect.width >= page_width * CONTAINER_SPAN_FRACTION:
            continue
        if page_height and rect.height >= page_height * CONTAINER_SPAN_FRACTION:
            continue
        candidates.append(rect)

    deduped: List["fitz.Rect"] = []
    for rect in candidates:
        if not any(_rects_close(rect, existing) for existing in deduped):
            deduped.append(rect)
    return deduped


def _assign_text_to_boxes(
    box_rects: List["fitz.Rect"], text_dict: Dict[str, Any], page_index: int
) -> List[Dict[str, Any]]:
    """Assign each text span to the SMALLEST box rect that contains it.

    Diagramming tools commonly draw a swimlane/table-row (or whole-table) rectangle underneath
    several individual task boxes. A span's center can therefore fall inside more than one
    candidate rect at once. Assigning it to every containing rect (the old behavior) merges an
    entire lane's worth of labels into one giant box; the smallest containing rect is always the
    most specific (innermost, real content) box, so ties resolve to it instead.
    """
    parts_by_index: Dict[int, List[Tuple[float, float, str]]] = {}
    for block in text_dict.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_rect = fitz.Rect(span.get("bbox", (0, 0, 0, 0)))
                center = fitz.Point((span_rect.x0 + span_rect.x1) / 2, (span_rect.y0 + span_rect.y1) / 2)
                text = str(span.get("text", "")).strip()
                if not text:
                    continue
                best_index = None
                best_area = None
                for idx, rect in enumerate(box_rects):
                    if not rect.contains(center):
                        continue
                    area = rect.width * rect.height
                    if best_area is None or area < best_area:
                        best_area = area
                        best_index = idx
                if best_index is None:
                    continue
                parts_by_index.setdefault(best_index, []).append((round(span_rect.y0, 1), span_rect.x0, text))

    boxes: List[Dict[str, Any]] = []
    for offset, rect in enumerate(box_rects):
        parts = sorted(parts_by_index.get(offset, []))
        text = " ".join(p[2] for p in parts).strip()
        text = " ".join(text.split())
        if not text:
            continue
        boxes.append(
            {
                "id": offset,
                "page": page_index,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "text": text,
            }
        )
    return boxes


def _extract_arrowhead_points(drawings: List[Dict[str, Any]]) -> List["fitz.Point"]:
    points: List["fitz.Point"] = []
    for drawing in drawings:
        items = drawing.get("items", [])
        rect = drawing.get("rect")
        if not rect or rect.width > ARROWHEAD_MAX_SIZE or rect.height > ARROWHEAD_MAX_SIZE:
            continue
        if len(items) < 2 or not all(item[0] in ("l", "c") for item in items):
            continue
        pts: List["fitz.Point"] = []
        for item in items:
            pts.extend([p for p in item[1:] if isinstance(p, fitz.Point)])
        if not pts:
            continue
        cx = sum(p.x for p in pts) / len(pts)
        cy = sum(p.y for p in pts) / len(pts)
        points.append(fitz.Point(cx, cy))
    return points


def _extract_connectors(drawings: List[Dict[str, Any]]) -> List[Tuple["fitz.Point", "fitz.Point"]]:
    connectors: List[Tuple["fitz.Point", "fitz.Point"]] = []
    for drawing in drawings:
        items = drawing.get("items", [])
        rect = drawing.get("rect")
        if not items:
            continue
        if _is_box_drawing(drawing) is not None:
            continue  # a box (straight rect or closed rounded-rect loop)
        if rect and rect.width <= ARROWHEAD_MAX_SIZE and rect.height <= ARROWHEAD_MAX_SIZE:
            continue  # likely an arrowhead or decoration
        if not all(item[0] in ("l", "c") for item in items):
            continue
        start = _item_start(items[0])
        end = _item_end(items[-1])
        if start is None or end is None:
            continue
        if _dist(start, end) < MIN_CONNECTOR_LENGTH and len(items) <= 1:
            continue
        connectors.append((start, end))
    return connectors


def extract_diagram_structure(pdf_bytes: bytes) -> Dict[str, Any] | None:
    """Best-effort extraction of box labels and their connecting arrows from a diagram-style PDF.

    Plain text extraction discards all positional/vector information, so it can never recover which
    box connects to which. This walks the PDF's vector drawing commands directly: rectangles become
    "boxes" (with their contained text as the label), thin line/curve paths become candidate
    "connectors", and small closed triangular fills near a connector's endpoint are treated as an
    arrowhead that resolves its direction. Returns None if PyMuPDF isn't available or nothing
    box-like was found (e.g. a prose document, or a diagram that doesn't draw real rectangle shapes).
    """
    if fitz is None:
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None

    all_boxes: List[Dict[str, Any]] = []
    all_connections: List[Dict[str, Any]] = []

    try:
        for page_index, page in enumerate(doc):
            if page_index >= MAX_PAGES:
                break
            try:
                drawings = page.get_drawings()
                text_dict = page.get_text("dict")
            except Exception:  # noqa: BLE001
                continue

            box_rects = _extract_box_rects(drawings, page.rect.width, page.rect.height)
            if not box_rects:
                continue
            boxes = _assign_text_to_boxes(box_rects, text_dict, page_index)
            if not boxes:
                continue
            for box in boxes:
                box["page_width"] = page.rect.width

            arrowheads = _extract_arrowhead_points(drawings)
            connectors = _extract_connectors(drawings)

            def _nearest_box(point: "fitz.Point") -> Dict[str, Any] | None:
                # Break distance ties (commonly 0, when a point sits inside more than one
                # remaining box rect) in favor of the smallest box -- the most specific match.
                def sort_key(b: Dict[str, Any]) -> tuple[float, float]:
                    rect = fitz.Rect(b["bbox"])
                    return (_point_to_rect_distance(point, rect), rect.width * rect.height)

                return min(boxes, key=sort_key, default=None)

            page_connections: List[Dict[str, Any]] = []
            for start, end in connectors:
                box_a = _nearest_box(start)
                box_b = _nearest_box(end)
                if not box_a or not box_b or box_a["id"] == box_b["id"]:
                    continue
                if _point_to_rect_distance(start, fitz.Rect(box_a["bbox"])) > MAX_ENDPOINT_TO_BOX_DISTANCE:
                    continue
                if _point_to_rect_distance(end, fitz.Rect(box_b["bbox"])) > MAX_ENDPOINT_TO_BOX_DISTANCE:
                    continue

                source_id, target_id, confidence = box_a["id"], box_b["id"], 0.55
                nearest_to_end = min(arrowheads, key=lambda h: _dist(h, end), default=None)
                nearest_to_start = min(arrowheads, key=lambda h: _dist(h, start), default=None)
                if nearest_to_end is not None and _dist(nearest_to_end, end) <= MAX_ARROWHEAD_TO_ENDPOINT_DISTANCE:
                    source_id, target_id, confidence = box_a["id"], box_b["id"], 0.95
                elif nearest_to_start is not None and _dist(nearest_to_start, start) <= MAX_ARROWHEAD_TO_ENDPOINT_DISTANCE:
                    source_id, target_id, confidence = box_b["id"], box_a["id"], 0.95

                page_connections.append({"source_id": source_id, "target_id": target_id, "confidence": confidence})

            # Box ids so far were local to this page (0..k-1, from _assign_text_to_boxes) so that
            # matching above could work on a clean, page-scoped list. Remap to globally unique ids
            # now -- offsets into box_rects can exceed the page's surviving box count once
            # container/empty-text candidates are filtered out, so reusing len(all_boxes) as a
            # per-page start_id (the previous approach) could collide with a later page's ids.
            id_map = {box["id"]: len(all_boxes) + local_id for local_id, box in enumerate(boxes)}
            for box in boxes:
                box["id"] = id_map[box["id"]]
            all_boxes.extend(boxes)
            for conn in page_connections:
                conn["source_id"] = id_map[conn["source_id"]]
                conn["target_id"] = id_map[conn["target_id"]]
            all_connections.extend(page_connections)
    finally:
        doc.close()

    if not all_boxes:
        return None

    return {"boxes": all_boxes, "connections": all_connections}
