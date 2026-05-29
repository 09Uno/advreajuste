from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _dec(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    if isinstance(v, str):
        v = v.strip().replace("R$", "").replace(" ", "")
        if "," in v and "." in v:
            v = v.replace(".", "").replace(",", ".")
        elif "," in v:
            v = v.replace(",", ".")
    return Decimal(str(v))


def _valida_cpf(v: str) -> str:
    v = re.sub(r"\D", "", v)
    if len(v) != 11 or v == v[0] * 11:
        raise ValueError("CPF inválido")

    def dv(base: str) -> int:
        s = sum(int(base[i]) * (len(base) + 1 - i) for i in range(len(base)))
        r = (s * 10) % 11
        return 0 if r == 10 else r

    if dv(v[:9]) != int(v[9]) or dv(v[:10]) != int(v[10]):
        raise ValueError("CPF com DV inválido")
    return v


class EventoTipo(str, Enum):
    ANUAL_ANS = "reajuste_anual_ans"
    ANUAL_CONTRATO = "reajuste_anual_contratual_aplicado"
    FAIXA_ETARIA = "faixa_etaria_rn63"
    DOWNGRADE = "downgrade_plano"
    SINISTRALIDADE = "reajuste_sinistralidade"
    POOL_RN565 = "reajuste_pool_rn565"
    DESCONHECIDO = "desconhecido"


class Beneficiario(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    nome: str = Field(min_length=3, max_length=120)
    cpf: str
    data_nascimento: date
    mensalidade_base: Decimal = Field(gt=Decimal("0"))
    titular: bool = True

    @field_validator("cpf")
    @classmethod
    def _cpf(cls, v: str) -> str:
        return _valida_cpf(v)

    @field_validator("mensalidade_base", mode="before")
    @classmethod
    def _mb(cls, v):
        return _dec(v)


class ParcelaCobrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    competencia: str = Field(pattern=r"^\d{4}-\d{2}$")
    valor_cobrado: Decimal = Field(ge=Decimal("0"))
    beneficiario_cpf: str | None = None
    operadora: str | None = None
    origem: str = "pdf"

    @field_validator("valor_cobrado", mode="before")
    @classmethod
    def _vc(cls, v):
        return _dec(v)


class Evento(BaseModel):
    model_config = ConfigDict(frozen=True)

    competencia: str = Field(pattern=r"^\d{4}-\d{2}$")
    tipo: EventoTipo
    percentual: Decimal
    origem: str = ""

    @field_validator("percentual", mode="before")
    @classmethod
    def _p(cls, v):
        return _dec(v)


class Boleto(BaseModel):
    model_config = ConfigDict(frozen=True)

    competencia: str = Field(pattern=r"^\d{4}-\d{2}$")
    data_vencimento: date | None = None
    valor_total: Decimal = Field(ge=Decimal("0"))
    reajuste_anual_pct: Decimal | None = None
    tipo_reajuste: Literal["anual", "faixa_etaria", "downgrade", "desconhecido"] = "desconhecido"
    operadora: str | None = None
    contrato: str | None = None
    beneficiarios: list[dict] = Field(default_factory=list)
    sha256_origem: str | None = None

    @field_validator("valor_total", "reajuste_anual_pct", mode="before")
    @classmethod
    def _n(cls, v):
        return None if v is None else _dec(v)


class Contrato(BaseModel):
    model_config = ConfigDict(frozen=True)

    numero: str
    operadora: str
    data_assinatura: date
    mensalidade_inicial: Decimal = Field(gt=Decimal("0"))
    tipo: Literal["individual", "coletivo_empresarial", "coletivo_adesao"] = "coletivo_empresarial"
    n_vidas: int = Field(ge=1)
    estipulante: str | None = None

    @field_validator("mensalidade_inicial", mode="before")
    @classmethod
    def _mi(cls, v):
        return _dec(v)

    @property
    def falso_coletivo(self) -> bool:
        return self.tipo != "individual" and self.n_vidas < 30

    @property
    def mes_aniversario(self) -> int:
        return self.data_assinatura.month


class Caso(BaseModel):
    model_config = ConfigDict(frozen=False)

    caso_id: str
    contrato: Contrato
    beneficiarios: list[Beneficiario]
    cobrancas: list[ParcelaCobrada] = Field(default_factory=list)
    indice_correcao: Literal["INPC", "IPCA", "SELIC", "IPCA-E"] = "INPC"
