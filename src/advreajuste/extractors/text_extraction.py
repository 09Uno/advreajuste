"""Camada de extração de texto unificada com cache e fallback OCR.

Encadeamento:
1. pdfplumber.extract_text() — formato padrão (rápido, preciso pra PDFs textuais)
2. pymupdf (fitz).get_text() — fallback (mais robusto pra PDFs com fontes custom)
3. OCR (tesseract via pytesseract + pdf2image) — para PDFs escaneados/imagens

Cache em memória por (path, mtime) — evita reabrir o mesmo PDF dentro
do mesmo processo (importante porque o router chama vários parsers que
cada um abria o PDF separadamente).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

from loguru import logger


_OCR_DISPONIVEL: bool | None = None


def _configurar_tesseract_windows():
    """Se Tesseract instalado em local padrão Windows, configura pytesseract."""
    if os.name != "nt":
        return
    try:
        import pytesseract
    except ImportError:
        return
    # Caminhos padrão da instalação Windows
    candidatos = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for caminho in candidatos:
        if Path(caminho).exists():
            pytesseract.pytesseract.tesseract_cmd = caminho
            # Define TESSDATA_PREFIX também
            tessdata = Path(caminho).parent / "tessdata"
            if tessdata.exists() and "TESSDATA_PREFIX" not in os.environ:
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            return


def _ocr_disponivel() -> bool:
    """Checa se tesseract está instalado (cacheado)."""
    global _OCR_DISPONIVEL
    if _OCR_DISPONIVEL is not None:
        return _OCR_DISPONIVEL
    _configurar_tesseract_windows()
    try:
        import pytesseract  # noqa
        import pdf2image  # noqa
        pytesseract.get_tesseract_version()
        _OCR_DISPONIVEL = True
    except Exception as e:
        logger.debug("OCR não disponível: {}", e)
        _OCR_DISPONIVEL = False
    return _OCR_DISPONIVEL


def _ocr_lang_disponivel() -> str:
    """Retorna 'por' se português disponível, senão 'eng'."""
    try:
        import pytesseract
        langs = pytesseract.get_languages()
        if "por" in langs:
            return "por"
    except Exception:
        pass
    return "eng"


def _extract_pdfplumber(pdf_path: Path) -> list[str]:
    """Texto por página via pdfplumber. Lista de strings (uma por página)."""
    import pdfplumber
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return [(pg.extract_text() or "") for pg in pdf.pages]
    except Exception as e:
        logger.warning("pdfplumber falhou em {}: {}", pdf_path.name, e)
        return []


def _extract_pymupdf(pdf_path: Path) -> list[str]:
    """Texto por página via PyMuPDF (fitz). Fallback mais robusto pra fontes
    custom com (cid:XX)."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        try:
            return [pg.get_text() or "" for pg in doc]
        finally:
            doc.close()
    except Exception as e:
        logger.warning("PyMuPDF falhou em {}: {}", pdf_path.name, e)
        return []


_POPPLER_PATH_CACHE: str | None = None


def _descobrir_poppler() -> str | None:
    """Descobre poppler_path no Windows (cacheado)."""
    global _POPPLER_PATH_CACHE
    if _POPPLER_PATH_CACHE is not None:
        return _POPPLER_PATH_CACHE
    if os.name != "nt":
        _POPPLER_PATH_CACHE = ""
        return None
    for candidato in [
        r"C:\Program Files\poppler\bin",
        r"C:\Program Files\poppler-25.07.0\Library\bin",
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages"),
    ]:
        p = Path(candidato)
        if p.exists():
            for f in p.rglob("pdftoppm.exe"):
                _POPPLER_PATH_CACHE = str(f.parent)
                return _POPPLER_PATH_CACHE
    _POPPLER_PATH_CACHE = ""
    return None


# Config Tesseract:
# --oem 1: engine LSTM (mais preciso que legacy)
# PSM default (3=auto) — outros PSMs perderam acurácia em recibos Qualicorp
TESSERACT_CONFIG = r"--oem 1"


def _ocr_dpi() -> int:
    try:
        return max(100, min(250, int(os.environ.get("ADVREAJUSTE_OCR_DPI", "150"))))
    except ValueError:
        return 150


def _extract_ocr(pdf_path: Path, dpi: int | None = None) -> list[str]:
    """OCR via pytesseract+pdf2image com baixo consumo de memória.

    Processa uma página por vez para não carregar todas as imagens do PDF em
    RAM. Isso é essencial no Streamlit Cloud, onde lotes grandes derrubam o
    processo se o OCR abrir vários PDFs/páginas em paralelo.
    """
    if not _ocr_disponivel():
        return []
    try:
        import pytesseract
        from pdf2image import convert_from_path, pdfinfo_from_path

        poppler_path = _descobrir_poppler()
        lang = _ocr_lang_disponivel()
        dpi = dpi or _ocr_dpi()
        kwargs = {"dpi": dpi, "grayscale": True, "thread_count": 1}
        if poppler_path:
            kwargs["poppler_path"] = poppler_path

        try:
            info = pdfinfo_from_path(str(pdf_path), poppler_path=poppler_path)
            n_pages = int(info.get("Pages", 1))
        except Exception:
            n_pages = 1

        textos = []
        for page in range(1, n_pages + 1):
            imgs = convert_from_path(
                str(pdf_path),
                first_page=page,
                last_page=page,
                **kwargs,
            )
            img = imgs[0] if imgs else None
            if img is None:
                textos.append("")
                continue
            try:
                txt = pytesseract.image_to_string(
                    img, lang=lang, config=TESSERACT_CONFIG,
                )
            except Exception:
                txt = pytesseract.image_to_string(img, config=TESSERACT_CONFIG)
            textos.append(txt)
            try:
                img.close()
            except Exception:
                pass
        return textos
    except Exception as e:
        logger.warning("OCR falhou em {}: {}", pdf_path.name, e)
        return []


# ──────────────── Cache em disco (persistente entre sessões) ────────────────

_DISCO_LOCK = threading.Lock()


def _hash_arquivo(pdf_path: Path) -> str:
    """SHA-1 do (path + mtime + tamanho) — chave estável do cache."""
    stat = pdf_path.stat()
    chave = f"{pdf_path.resolve()}|{stat.st_mtime}|{stat.st_size}"
    return hashlib.sha1(chave.encode()).hexdigest()[:16]


def _cache_disco_dir() -> Path:
    """Diretório de cache OCR — em ~/.advreajuste/ocr_cache/ por padrão."""
    base = Path(os.environ.get("ADVREAJUSTE_OCR_CACHE_DIR",
                                os.path.expanduser("~/.advreajuste/ocr_cache")))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _carregar_cache_disco(pdf_path: Path) -> list[str] | None:
    """Carrega texto OCR do disco se existir e estiver válido."""
    try:
        cache_file = _cache_disco_dir() / f"{_hash_arquivo(pdf_path)}.json"
        if cache_file.exists():
            with _DISCO_LOCK, open(cache_file, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Cache disco falhou em {}: {}", pdf_path.name, e)
    return None


def _salvar_cache_disco(pdf_path: Path, paginas: list[str]):
    """Salva texto OCR no disco pra reuso entre sessões."""
    try:
        cache_file = _cache_disco_dir() / f"{_hash_arquivo(pdf_path)}.json"
        with _DISCO_LOCK, open(cache_file, "w", encoding="utf-8") as f:
            json.dump(paginas, f, ensure_ascii=False)
    except Exception as e:
        logger.debug("Salvar cache OCR falhou em {}: {}", pdf_path.name, e)


def _key(pdf_path: Path) -> tuple[str, float]:
    """Chave do cache: (caminho, mtime)."""
    try:
        stat = pdf_path.stat()
        return (str(pdf_path), stat.st_mtime)
    except OSError:
        return (str(pdf_path), 0.0)


# Cache em memória dos textos extraídos por PDF
_cache_paginas: dict[tuple[str, float], list[str]] = {}
_cache_metodo: dict[tuple[str, float], str] = {}


def extrair_paginas(
    pdf_path: Path,
    forcar_ocr: bool = False,
    permitir_ocr: bool = True,
) -> tuple[list[str], str]:
    """Extrai texto de cada página de um PDF.

    Retorna `(paginas, metodo)` onde método é 'pdfplumber', 'pymupdf' ou 'ocr'.

    Estratégia (cada uma só é tentada se a anterior falhar):
    1. pdfplumber (rápido, melhor pra tabelas)
    2. pymupdf (fallback pra fontes custom)
    3. OCR (se permitir_ocr e tesseract disponível)
    """
    key = _key(pdf_path)
    if key in _cache_paginas and not forcar_ocr:
        return _cache_paginas[key], _cache_metodo[key]

    # 1. pdfplumber
    if not forcar_ocr:
        paginas = _extract_pdfplumber(pdf_path)
        chars_totais = sum(len(p) for p in paginas)
        if chars_totais > 50:  # texto significativo
            _cache_paginas[key] = paginas
            _cache_metodo[key] = "pdfplumber"
            return paginas, "pdfplumber"

        # 2. pymupdf
        paginas_fitz = _extract_pymupdf(pdf_path)
        chars_fitz = sum(len(p) for p in paginas_fitz)
        if chars_fitz > chars_totais:
            paginas = paginas_fitz
            chars_totais = chars_fitz
        if chars_totais > 50:
            _cache_paginas[key] = paginas
            _cache_metodo[key] = "pymupdf"
            return paginas, "pymupdf"

    # 3. OCR — primeiro checa cache de disco
    if permitir_ocr:
        paginas_disco = _carregar_cache_disco(pdf_path)
        if paginas_disco and sum(len(p) for p in paginas_disco) > 50:
            _cache_paginas[key] = paginas_disco
            _cache_metodo[key] = "ocr_cache"
            return paginas_disco, "ocr_cache"

        paginas_ocr = _extract_ocr(pdf_path)
        if paginas_ocr and sum(len(p) for p in paginas_ocr) > 50:
            # Aplica normalizações comuns de erros de OCR
            paginas_ocr = [normalizar_ocr(p) for p in paginas_ocr]
            _cache_paginas[key] = paginas_ocr
            _cache_metodo[key] = "ocr"
            # Persiste no disco pra reuso entre sessões
            _salvar_cache_disco(pdf_path, paginas_ocr)
            return paginas_ocr, "ocr"

    # 4. Desistiu — devolve o que tiver (ou lista vazia)
    paginas = _cache_paginas.get(key, [])
    _cache_paginas[key] = paginas
    _cache_metodo[key] = "vazio"
    return paginas, "vazio"


def texto_completo(pdf_path: Path, permitir_ocr: bool = True) -> str:
    """Concatena todas as páginas em um único string."""
    paginas, _ = extrair_paginas(pdf_path, permitir_ocr=permitir_ocr)
    return "\n".join(paginas)


def ocr_em_paralelo(
    pdfs: list[Path],
    max_workers: int = 1,
    progress_cb=None,
) -> dict[Path, str]:
    """Processa OCR de múltiplos PDFs em paralelo (ThreadPool).

    Tesseract libera o GIL (spawn de processo externo), então
    ThreadPool dá speedup linear até ~4-8 workers em CPUs modernas.
    Acima disso o disco/RAM viram gargalo.

    Retorna `{pdf_path: metodo_usado}` para tracking.
    """
    if not pdfs:
        return {}

    resultados: dict[Path, str] = {}

    def _processar(pdf: Path) -> tuple[Path, str]:
        try:
            _, metodo = extrair_paginas(pdf, forcar_ocr=True)
            return pdf, metodo
        except Exception as e:
            logger.warning("OCR paralelo falhou em {}: {}", pdf.name, e)
            return pdf, "erro"

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_processar, pdf): pdf for pdf in pdfs}
        for future in as_completed(futures):
            pdf, metodo = future.result()
            resultados[pdf] = metodo
            completed += 1
            if progress_cb:
                try:
                    progress_cb(completed, len(pdfs), pdf.name)
                except Exception:
                    pass

    return resultados


_cache_escaneado: dict[tuple[str, float], bool] = {}


def eh_escaneado(pdf_path: Path) -> bool:
    """Detecta se o PDF é apenas imagem (sem texto extraível). Cacheado."""
    key = _key(pdf_path)
    if key in _cache_escaneado:
        return _cache_escaneado[key]

    # Usa pymupdf (3-5x mais rápido que pdfplumber pra só checar texto)
    try:
        import fitz
        doc = fitz.open(pdf_path)
        chars = 0
        for pg in doc:
            chars += len(pg.get_text() or "")
            if chars > 50:
                break
        doc.close()
        resultado = chars < 50
    except Exception:
        resultado = False  # se não consegue abrir, deixa parsers tentarem

    _cache_escaneado[key] = resultado
    return resultado


def limpar_cache():
    """Limpa o cache de PDFs em memória."""
    _cache_paginas.clear()
    _cache_metodo.clear()
    _cache_escaneado.clear()


# ────────────── Normalização de texto OCR ──────────────
import re as _re

_NORMALIZACOES_OCR = [
    # R$ frequentemente vira RS ou R5 no OCR
    (_re.compile(r"\bR[S5]\s+(?=\d)"), "R$ "),
    # Cifrão isolado errado
    (_re.compile(r"\bR\s*(?=\d{1,3}[.,])"), "R$ "),
    # Aspas decorativas viram lixo
    (_re.compile(r"[“”„‟]"), '"'),
    # Hífen unicode → ascii
    (_re.compile(r"[‐‑‒–—―]"), "-"),
    # Pipes/colchetes/aspas no início ou fim de linha (bordas de tabela)
    (_re.compile(r"(?m)^[\[\|\"'\s]+"), ""),
    (_re.compile(r"(?m)[\]\|\"'\s]+$"), ""),
    # OCR às vezes adiciona I/|/l antes do nome operadora
    # IBRADESCO → BRADESCO, |AMIL → AMIL etc.
    (_re.compile(r"(?<= )[Il\|](?=BRADESCO|AMIL|UNIMED|HAPVIDA|CASSI|SUL|NOTRE|CARE|PORTO|ALLIANZ|MEDISERVICE|OMINT|GEAP)"), ""),
    # JOAO → oAao (corrige inversão típica do scanner que rotacionou)
    # Mantemos genérico: se nome começa com letra minúscula, capitalizar
    # (feito via pós-processamento depois, não regex)
    # Acentos perdidos
    (_re.compile(r"\bImport[âaã]ncia\b", _re.I), "importância"),
    (_re.compile(r"\bComp[eé]t[eé]ncia\b", _re.I), "Competência"),
    (_re.compile(r"\bBeneficio\b", _re.I), "Benefício"),
    (_re.compile(r"\bBeneficiario\b", _re.I), "Beneficiário"),
]


def normalizar_ocr(texto: str) -> str:
    """Aplica correções comuns de erros de OCR pra texto extraído via OCR."""
    if not texto:
        return texto
    out = texto
    for regex, sub in _NORMALIZACOES_OCR:
        out = regex.sub(sub, out)
    return out
