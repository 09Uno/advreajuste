"""Camada 3: Claude Sonnet Vision via Anthropic SDK + Instructor → Boleto Pydantic."""
from __future__ import annotations

import base64
from pathlib import Path

from ..llm.client import instructor_client
from ..config import settings
from .schemas import Boleto


PROMPT = """Você é um extrator determinístico de boletos/faturas de plano de saúde brasileiro.
Extraia APENAS dados literais do documento — nunca estime. Se um campo não aparece, retorne null.
Valores em BRL devem ser extraídos como número decimal (ex: 1234.56).
Competência no formato YYYY-MM (correspondente ao mês de referência da cobrança, não ao vencimento).
Beneficiários: extraia como lista de objetos {nome, cpf, data_nascimento, valor_individual}.
"""


def extrair_com_vision(pdf_path: Path) -> Boleto:
    client = instructor_client()
    pdf_bytes = Path(pdf_path).read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return client.messages.create(
        model=settings.llm_model_vision,
        max_tokens=4096,
        max_retries=3,
        response_model=Boleto,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
