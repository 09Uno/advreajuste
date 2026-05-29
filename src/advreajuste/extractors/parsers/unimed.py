from pathlib import Path
from ..schemas import Boleto
from ._generic import parse_generic


def parse(pdf_path: Path) -> Boleto | None:
    # Unimed/Hapvida tendem a ter grade nítida — usar Camelot lattice em calibração futura.
    return parse_generic(pdf_path, operadora="Unimed")
