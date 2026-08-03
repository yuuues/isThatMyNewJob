from datetime import UTC, datetime

import httpx

from app.dedup import normaliza
from app.schemas import RawJob, SearchQuery
from app.sources.remotive import html_a_texto

URL_API = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource:
    """Feed de ofertas, mayoritariamente del mercado alemán.

    Como Remotive, no filtra por texto en servidor: pagina un feed completo de 175
    ofertas por página. El filtrado por texto se hace aquí. El prefiltro de idioma
    es especialmente relevante para esta fuente.
    """

    nombre = "arbeitnow"

    def __init__(self, max_paginas: int = 1, timeout: float = 30.0) -> None:
        self.max_paginas = max_paginas
        self._timeout = timeout

    def search(self, query: SearchQuery) -> list[RawJob]:
        ofertas: list[RawJob] = []
        for pagina in range(1, self.max_paginas + 1):
            datos = self._descargar(pagina)
            ofertas.extend(self._normaliza(j) for j in datos.get("data", []))
            if not (datos.get("links") or {}).get("next"):
                break

        if query.solo_remoto:
            ofertas = [o for o in ofertas if o.modalidad == "remoto"]

        return self._filtra(ofertas, query)[: query.max_resultados]

    def _descargar(self, pagina: int) -> dict:
        respuesta = httpx.get(URL_API, params={"page": str(pagina)}, timeout=self._timeout)
        respuesta.raise_for_status()
        return respuesta.json()

    def _normaliza(self, bruto: dict) -> RawJob:
        return RawJob(
            fuente=self.nombre,
            external_id=bruto["slug"],
            url=bruto["url"],
            titulo=bruto["title"],
            empresa=bruto["company_name"],
            ubicacion=bruto.get("location") or None,
            modalidad="remoto" if bruto.get("remote") else "presencial",
            descripcion=html_a_texto(bruto.get("description", "")),
            publicada_en=self._fecha(bruto.get("created_at")),
            tags=bruto.get("tags") or [],
        )

    @staticmethod
    def _fecha(valor: int | None) -> datetime | None:
        if not valor:
            return None
        return datetime.fromtimestamp(valor, tz=UTC).replace(tzinfo=None)

    @staticmethod
    def _filtra(ofertas: list[RawJob], query: SearchQuery) -> list[RawJob]:
        terminos = normaliza(query.texto).split()
        if not terminos:
            return ofertas

        resultado = []
        for oferta in ofertas:
            heno = normaliza(
                f"{oferta.titulo} {oferta.descripcion} {' '.join(oferta.tags)} {oferta.ubicacion or ''}"
            )
            if any(t in heno for t in terminos):
                resultado.append(oferta)
        return resultado
