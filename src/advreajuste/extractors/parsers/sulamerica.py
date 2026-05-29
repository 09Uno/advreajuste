from pathlib import Path
from ..schemas import Boleto
from ._generic import parse_generic


def parse(pdf_path: Path) -> Boleto | None:
    # TODO: calibrar com fixture real de boleto SulAmérica (table_settings customizados).
    return parse_generic(pdf_path, operadora="SulAmerica")
