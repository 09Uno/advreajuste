import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..config import settings
from .hashing import sha256_file


def _caso_log(caso_id: str) -> Path:
    d = settings.custody_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{caso_id}.jsonl"


def registrar_evento(caso_id: str, evento: str, payload: dict[str, Any]) -> dict[str, Any]:
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "caso": caso_id,
        "evento": evento,
        **payload,
    }
    with _caso_log(caso_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def registrar_original(path: Path, caso_id: str) -> dict[str, Any]:
    p = Path(path)
    return registrar_evento(
        caso_id,
        "ingesta",
        {
            "arquivo": p.name,
            "sha256": sha256_file(p),
            "tamanho": p.stat().st_size,
            "caminho": str(p.resolve()),
        },
    )


def ler_eventos(caso_id: str) -> Iterator[dict[str, Any]]:
    log = _caso_log(caso_id)
    if not log.exists():
        return
    with log.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
