from datetime import UTC, datetime

import httpx

from app.dedup import normaliza
from app.limitador import LimitadorPorHost
from app.schemas import RawJob, SearchQuery
from app.sources.base import FuenteFiltradaEnLocal
from app.sources.remotive import html_a_texto

URL_API = "https://www.arbeitnow.com/api/job-board-api"
INTERVALO_SEGUNDOS = 2.0


class ArbeitnowSource(FuenteFiltradaEnLocal):
    """Feed de ofertas, mayoritariamente del mercado alemán.

    Como Remotive, no filtra por texto en servidor: pagina un feed completo de 175
    ofertas por página. El filtrado por texto se hace aquí. El prefiltro de idioma
    es especialmente relevante para esta fuente.
    """

    nombre = "arbeitnow"

    def __init__(
        self,
        max_paginas: int = 1,
        timeout: float = 30.0,
        limitador: LimitadorPorHost | None = None,
    ) -> None:
        self.max_paginas = max_paginas
        self._timeout = timeout
        self._limitador = limitador or LimitadorPorHost(
            intervalo_por_defecto=INTERVALO_SEGUNDOS
        )

    def _descarga_feed(self) -> list[RawJob]:
        ofertas: list[RawJob] = []
        for pagina in range(1, self.max_paginas + 1):
            datos = self._descargar(pagina)
            ofertas.extend(self._normaliza(j) for j in datos.get("data", []))
            if not (datos.get("links") or {}).get("next"):
                break
        return ofertas

    def _aplica_query(self, ofertas: list[RawJob], query: SearchQuery) -> list[RawJob]:
        # `solo_remoto` es de cada búsqueda, no del feed: se aplica por búsqueda para
        # que una búsqueda restrictiva no recorte a las demás.
        if query.solo_remoto:
            ofertas = [o for o in ofertas if o.modalidad == "remoto"]
        return self._filtra(ofertas, query)[: query.max_resultados]

    def _descargar(self, pagina: int) -> dict:
        self._limitador.espera_turno(URL_API)
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
