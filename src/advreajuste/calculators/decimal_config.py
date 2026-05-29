from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, getcontext

getcontext().prec = 28

BRL = Decimal("0.01")
IDX = Decimal("0.00000001")
PCT = Decimal("0.0001")


def to_decimal(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def brl(x) -> Decimal:
    return to_decimal(x).quantize(BRL, rounding=ROUND_HALF_UP)


def fator(x) -> Decimal:
    return to_decimal(x).quantize(IDX, rounding=ROUND_HALF_EVEN)


def pct(x) -> Decimal:
    return to_decimal(x).quantize(PCT, rounding=ROUND_HALF_EVEN)
