from pathlib import Path
from ..schemas import Boleto
from ._generic import parse_generic


def parse(pdf_path: Path) -> Boleto | None:
    return parse_generic(pdf_path, operadora="Hapvida")
