"""CLI Typer — pipeline consolidado com metodologia de substituição."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import pipeline
from .calculators.substituicao import totalizar_substituicao
from .logging_setup import setup_logging

app = typer.Typer(help="Pipeline de reajuste abusivo (STJ Tema 1016 / RN 565/2022)")
console = Console()


@app.callback()
def _init():
    setup_logging()


@app.command()
def extrair(
    pasta_pdfs: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    caso: str = typer.Option(..., "--caso"),
    parser: str = typer.Option("sulamerica_pme", "--parser",
                               help="sulamerica_pme | hibrido"),
):
    """Extrai PDFs para boletos_extraidos.json."""
    res = pipeline.ingerir_pdfs(caso, pasta_pdfs, parser_operadora=parser)
    if parser == "sulamerica_pme" and res:
        console.print(f"[green]{res[0]['n_linhas']} linhas / {res[0]['n_beneficiarios']} vidas[/]")
    else:
        console.print(f"[green]{len(res)} PDFs processados.[/]")


@app.command()
def construir_caso(
    caso_id: str = typer.Argument(...),
    apolice: str = typer.Option(...),
    estipulante: str = typer.Option(...),
    mes_aniversario: int = typer.Option(..., min=1, max=12),
    vigencia: str = typer.Option(..., help="dd/mm/yyyy"),
    tipo: str = typer.Option("coletivo_empresarial"),
):
    """A partir da extração SulAmérica PME, monta e salva o Caso JSON."""
    from .config import settings
    import json as _json

    dt = date(*reversed([int(x) for x in vigencia.split("/")]))
    extr_path = settings.casos_dir / caso_id / "boletos_extraidos.json"
    extr = _json.loads(extr_path.read_text(encoding="utf-8"))[0]
    c = pipeline.construir_caso_sulamerica_pme(
        caso_id=caso_id, pdfs_extracao=extr, numero_apolice=apolice,
        estipulante=estipulante, mes_aniversario=mes_aniversario,
        data_vigencia=dt, tipo_plano=tipo,  # type: ignore
    )
    pipeline.salvar_caso(c)
    console.print(f"[green]Caso salvo. Falso coletivo? {c.contrato.falso_coletivo}[/]")


@app.command()
def calcular(
    caso: str = typer.Argument(...),
    data_acao: str = typer.Option(None, help="dd/mm/yyyy (default: hoje)"),
):
    """Executa motor de substituição + mostra resumo."""
    c = pipeline.carregar_caso(caso)
    dt = date(*reversed([int(x) for x in data_acao.split("/")])) if data_acao else date.today()
    r = pipeline.calcular(c, data_distribuicao_acao=dt)
    t = Table(title=f"Resumo — {caso}")
    for col in ["Beneficiário", "Meses", "Abusivos", "Pago", "Devido", "Δ", "Restituível 3a"]:
        t.add_column(col)
    for b, ls in r.items():
        tot = totalizar_substituicao(ls)
        t.add_row(
            b.nome, str(tot["n_meses"]), str(tot["n_aniversarios_abusivos"]),
            f"R$ {tot['total_pago']:,.2f}", f"R$ {tot['total_devido']:,.2f}",
            f"R$ {tot['diferenca']:,.2f}", f"R$ {tot['restituivel_simples']:,.2f}",
        )
    console.print(t)


@app.command()
def planilha(
    caso: str = typer.Argument(...),
    data_acao: str = typer.Option(None),
    indice: str = typer.Option("TJSP"),
):
    """Gera planilha pericial XLSX + correção monetária multi-índice."""
    c = pipeline.carregar_caso(caso)
    dt = date(*reversed([int(x) for x in data_acao.split("/")])) if data_acao else date.today()
    out = pipeline.executar_caso_completo(c, data_distribuicao_acao=dt, indice_principal=indice)  # type: ignore
    console.print(f"[green]Planilha:[/] {out['xlsx']}")
    console.print(f"[green]Relatório correções:[/] {out['relatorio']}")
    if out["docx"]:
        console.print(f"[yellow]Minuta (revisar):[/] {out['docx']}")


@app.command()
def peticao(caso: str = typer.Argument(...)):
    c = pipeline.carregar_caso(caso)
    r = pipeline.calcular(c)
    corr = pipeline.correcao_monetaria_agregada(r, date.today())
    out = pipeline.gerar_peticao_caso(c, r, corr)
    console.print(f"[yellow]Minuta:[/] {out}")


@app.command()
def custody(caso: str = typer.Argument(...)):
    from .custody import ler_eventos
    for ev in ler_eventos(caso):
        console.print_json(data=ev)


@app.command()
def ui():
    streamlit_app = Path(__file__).resolve().parents[2] / "streamlit_app.py"
    subprocess.run(["streamlit", "run", str(streamlit_app)])


if __name__ == "__main__":
    app()
