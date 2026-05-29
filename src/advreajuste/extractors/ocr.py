"""Camada 2 (fallback escaneados): Tesseract + OpenCV preprocess.

Requer `pip install advreajuste[ocr]` e Tesseract binário + `-l por` instalado.
"""
from __future__ import annotations

from pathlib import Path


def ocr_pagina(pdf_path: Path, dpi: int = 300) -> str:
    try:
        import cv2
        import numpy as np
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as e:
        raise RuntimeError(
            "Dependências de OCR ausentes. Instale com `uv sync --extra ocr` "
            "e Tesseract binário com pacote `por`."
        ) from e

    def preprocess(img_bgr):
        g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        g = cv2.fastNlMeansDenoising(g, h=10)
        coords = np.column_stack(np.where(g < 128))
        if len(coords) > 0:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            h, w = g.shape
            M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            g = cv2.warpAffine(
                g, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
        return cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
        )

    pages = convert_from_path(str(pdf_path), dpi=dpi)
    cfg = "--oem 1 --psm 6 -l por -c preserve_interword_spaces=1"
    out: list[str] = []
    for pil in pages:
        prep = preprocess(cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
        out.append(pytesseract.image_to_string(prep, config=cfg))
    return "\n".join(out)
