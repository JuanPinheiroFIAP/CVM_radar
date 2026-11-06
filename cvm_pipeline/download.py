"""Download incremental dos arquivos abertos da CVM."""
import json
import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import BASE_URL

log = logging.getLogger("cvm")


def _sessao() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "cvm-pipeline/1.0 (uso interno)"
    return s


_SESSAO = _sessao()


def baixar(caminho: str, pasta: Path, obrigatorio: bool = True) -> Path | None:
    """Baixa `caminho` (relativo a dados.cvm.gov.br/dados) para `pasta`.

    A CVM republica os arquivos todo dia com correções, então só baixa de novo quando
    Last-Modified/tamanho mudaram no servidor. Com `obrigatorio=False`, devolve None se o
    arquivo ainda não foi publicado (404), ex.: mês recém-iniciado.
    """
    url = f"{BASE_URL}/{caminho}"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / url.rsplit("/", 1)[1]
    meta = destino.with_name(destino.name + ".meta")

    cab = _SESSAO.head(url, timeout=60, allow_redirects=True)
    if cab.status_code == 404 and not obrigatorio:
        log.warning("ainda não publicado: %s", caminho)
        return None
    cab.raise_for_status()
    remoto = {"last_modified": cab.headers.get("Last-Modified"),
              "tamanho": cab.headers.get("Content-Length")}

    if destino.exists() and meta.exists() and any(remoto.values()):
        if json.loads(meta.read_text()) == remoto:
            log.info("em dia: %s", destino.name)
            return destino

    tmp = destino.with_name(destino.name + ".part")
    with _SESSAO.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        comprimido = bool(r.headers.get("Content-Encoding"))
        with open(tmp, "wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
    if remoto["tamanho"] and not comprimido and tmp.stat().st_size != int(remoto["tamanho"]):
        tmp.unlink()
        raise IOError(f"download incompleto: {url}")
    tmp.replace(destino)
    meta.write_text(json.dumps(remoto))
    log.info("baixado: %s (%.1f MB)", destino.name, destino.stat().st_size / 1e6)
    return destino
