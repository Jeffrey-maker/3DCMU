"""Rasterize a PDF page to PNG using PyMuPDF's own renderer.

Deliberately not pdf2image/poppler: PyMuPDF is already a hard dependency for
vector extraction, and poppler (pdftoppm/pdftocairo) is a system-level
dependency this machine doesn't have installed. get_pixmap() is sufficient
for a simple single-page architectural drawing — zero new dependencies.
"""

import fitz  # PyMuPDF


def render_to_png(pdf_path: str, out_path: str, page_number: int = 0, dpi: int = 150) -> None:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        pix = page.get_pixmap(dpi=dpi)
        pix.save(out_path)
    finally:
        doc.close()


def render_to_bytes(pdf_path: str, page_number: int = 0, dpi: int = 150) -> bytes:
    """In-memory variant for reference images that are never persisted to disk
    (e.g. the space-type report handed to Gemini alongside the floor raster)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number]
        return page.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
