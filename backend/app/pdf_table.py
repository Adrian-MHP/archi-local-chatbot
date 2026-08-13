from __future__ import annotations

from typing import Any, Dict, List

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

MAX_PAGES = 6

# Header aliases (normalized: lowercased, whitespace-collapsed) that identify a SIPOC-style
# detailed process table. "process step" / "prozessschritt" is the load-bearing signal -- a
# plain data table without it isn't treated as a SIPOC table at all.
_STEP_NAME_HEADERS = {"process step", "prozessschritt", "schritt", "activity", "aktivitat", "task"}
_ID_HEADERS = {"id", "nr", "nr.", "lfd nr", "step"}
_SUPPLIER_HEADERS = {"supplier", "lieferant", "s - supplier"}
_INPUT_HEADERS = {"input", "eingabe", "i - input"}
_PROCESS_DESC_HEADERS = {"process", "prozess", "beschreibung", "description", "p - process"}
_OUTPUT_HEADERS = {"output", "ausgabe", "ergebnis", "o - output"}
_CUSTOMER_HEADERS = {"customer", "kunde", "c - customer"}


def pdf_table_support_available() -> bool:
    return fitz is not None


def _normalize_header(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    text = text.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return " ".join(text.split())


def _normalize_cell(raw: Any) -> str:
    text = str(raw or "").strip()
    return " ".join(text.split())


def _find_column(headers: List[str], aliases: set[str]) -> int | None:
    for idx, header in enumerate(headers):
        if header in aliases:
            return idx
    return None


def extract_sipoc_steps(pdf_bytes: bytes) -> Dict[str, Any] | None:
    """Best-effort extraction of a SIPOC-style detailed process table's step chain.

    A SIPOC table lists one process step per row (an ID, a short step name, and
    supplier/input/process/output/customer columns) rather than drawing a diagram --
    PyMuPDF's own table detector (ruled lines/columns) is the right tool here, not the
    box/arrow vector-geometry heuristics used for diagrams. Returns None if PyMuPDF isn't
    available or no table with a recognizable "process step" column was found.
    """
    if fitz is None:
        return None

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None

    steps: List[Dict[str, str]] = []
    matched_header_signature: tuple[str, ...] | None = None

    try:
        for page_index, page in enumerate(doc):
            if page_index >= MAX_PAGES:
                break
            try:
                found = page.find_tables()
            except Exception:  # noqa: BLE001
                continue

            for table in found.tables:
                try:
                    rows = table.extract()
                except Exception:  # noqa: BLE001
                    continue
                if len(rows) < 2:
                    continue

                headers = [_normalize_header(h) for h in rows[0]]
                step_col = _find_column(headers, _STEP_NAME_HEADERS)
                if step_col is None:
                    continue  # not a SIPOC step table -- e.g. the 2-column SIPOC overview table

                signature = tuple(headers)
                if matched_header_signature is not None and signature != matched_header_signature:
                    # A different table shape (e.g. a second, unrelated SIPOC table further in
                    # the document). Keep the first chain found rather than mixing two processes.
                    continue
                matched_header_signature = signature

                id_col = _find_column(headers, _ID_HEADERS)
                supplier_col = _find_column(headers, _SUPPLIER_HEADERS)
                input_col = _find_column(headers, _INPUT_HEADERS)
                process_col = _find_column(headers, _PROCESS_DESC_HEADERS)
                output_col = _find_column(headers, _OUTPUT_HEADERS)
                customer_col = _find_column(headers, _CUSTOMER_HEADERS)

                def cell(row: List[Any], idx: int | None) -> str:
                    if idx is None or idx >= len(row):
                        return ""
                    return _normalize_cell(row[idx])

                for row in rows[1:]:
                    name = cell(row, step_col)
                    if not name:
                        continue
                    steps.append(
                        {
                            "id": cell(row, id_col),
                            "name": name,
                            "supplier": cell(row, supplier_col),
                            "input": cell(row, input_col),
                            "process": cell(row, process_col),
                            "output": cell(row, output_col),
                            "customer": cell(row, customer_col),
                        }
                    )
    finally:
        doc.close()

    if not steps:
        return None

    return {"steps": steps}
