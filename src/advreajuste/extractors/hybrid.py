"""Roteador cascata: template → OCR → Claude Vision."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from loguru import logger

from .pdf_template import extrair as extrair_template
from .schemas import Boleto


def extrair(
    pdf_path: Path,
    threshold: float = 0.85,
    usar_vision: bool = True,
) -> tuple[Boleto, Literal["template", "vision", "vazio"]]:
    b, conf = extrair_template(pdf_path)
    if b and conf >= threshold:
        logger.debug("template ok conf={:.2f} {}", conf, pdf_path.name)
        return b, "template"
    if usar_vision:
        try:
            from .pdf_vision import extrair_com_vision

            logger.info("fallback vision {}", pdf_path.name)
            bv = extrair_com_vision(pdf_path)
            return bv, "vision"
        except Exception as e:
            logger.warning("vision falhou em {}: {}", pdf_path.name, e)
    if b:
        return b, "template"
    raise RuntimeError(f"Não foi possível extrair {pdf_path}")
