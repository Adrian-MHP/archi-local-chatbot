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


def _extract_box_rects(drawings: List[Dict[str, Any]]) -> List["fitz.Rect"]:
    candidates: List["fitz.Rect"] = []
    for drawing in drawings:
        rect = _is_box_drawing(drawing)
        if rect is not None:
            candidates.append(rect)

    deduped: List["fitz.Rect"] = []
    for rect in candidates:
        if not any(_rects_close(rect, existing) for existing in deduped):
            deduped.append(rect)
    return deduped


def _assign_text_to_boxes(
    box_rects: List["fitz.Rect"], text_dict: Dict[str, Any], page_index: int, start_id: int
) -> List[Dict[str, Any]]:
    boxes: List[Dict[str, Any]] = []
    for offset, rect in enumerate(box_rects):
        parts: List[Tuple[float, float, str]] = []
        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_rect = fitz.Rect(span.get("bbox", (0, 0, 0, 0)))
                    center = fitz.Point((span_rect.x0 + span_rect.x1) / 2, (span_rect.y0 + span_rect.y1) / 2)
                    if rect.contains(center):
                        text = str(span.get("text", "")).strip()
                        if text:
                            parts.append((round(span_rect.y0, 1), span_rect.x0, text))
        parts.sort()
        text = " ".join(p[2] for p in parts).strip()
        text = " ".join(text.split())
        if not text:
            continue
        boxes.append(
            {
                "id": start_id + offset,
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

            box_rects = _extract_box_rects(drawings)
            if not box_rects:
                continue
            boxes = _assign_text_to_boxes(box_rects, text_dict, page_index, start_id=len(all_boxes))
            if not boxes:
                continue

            arrowheads = _extract_arrowhead_points(drawings)
            connectors = _extract_connectors(drawings)

            for start, end in connectors:
                box_a = min(boxes, key=lambda b: _point_to_rect_distance(start, fitz.Rect(b["bbox"])), default=None)
                box_b = min(boxes, key=lambda b: _point_to_rect_distance(end, fitz.Rect(b["bbox"])), default=None)
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

                all_connections.append({"source_id": source_id, "target_id": target_id, "confidence": confidence})

            all_boxes.extend(boxes)
    finally:
        doc.close()

    if not all_boxes:
        return None

    return {"boxes": all_boxes, "connections": all_connections}
