import re
from datetime import datetime
from html import unescape

import httpx

from app.dedup import normaliza
from app.limitador import LimitadorPorHost
from app.schemas import RawJob, SearchQuery
from app.sources.base import FuenteFiltradaEnLocal

URL_API = "https://remotive.com/api/remote-jobs"

# Su aviso legal pide ~4 peticiones al día: se separan bien las que haya.
INTERVALO_SEGUNDOS = 5.0
_TAGS_HTML = re.compile(r"<[^>]+>")


def html_a_texto(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAGS_HTML.sub(" ", html or ""))).strip()


class RemotiveSource(FuenteFiltradaEnLocal):
    """Feed de ofertas remotas.

    La API ignora `search` y `limit`: devuelve siempre el feed reciente completo.
    Por eso el filtrado por texto se hace aquí y `filtra_en_servidor` es False: la
    ingesta descarga el feed una vez por run y lo filtra contra todas las búsquedas.
    """

    nombre = "remotive"

    def __init__(
        self,
        cliente: httpx.Client | None = None,
        timeout: float = 30.0,
        limitador: LimitadorPorHost | None = None,
    ) -> None:
        self._cliente = cliente
        self._timeout = timeout
        self._limitador = limitador or LimitadorPorHost(
            intervalo_por_defecto=INTERVALO_SEGUNDOS
        )

    def _descarga_feed(self) -> list[RawJob]:
        datos = self._descargar()
        return [self._normaliza(j) for j in datos.get("jobs", [])]

    def _aplica_query(self, ofertas: list[RawJob], query: SearchQuery) -> list[RawJob]:
        return self._filtra(ofertas, query)[: query.max_resultados]

    def _descargar(self) -> dict:
        self._limitador.espera_turno(URL_API)
        if self._cliente is not None:
            respuesta = self._cliente.get(URL_API, timeout=self._timeout)
        else:
            respuesta = httpx.get(URL_API, timeout=self._timeout)
        respuesta.raise_for_status()
        return respuesta.json()

    def _normaliza(self, bruto: dict) -> RawJob:
        return RawJob(
            fuente=self.nombre,
            external_id=str(bruto["id"]),
            url=bruto["url"],
            titulo=bruto["title"],
            empresa=bruto["company_name"],
            ubicacion=bruto.get("candidate_required_location") or None,
            modalidad="remoto",
            salario_texto=bruto.get("salary") or None,
            descripcion=html_a_texto(bruto.get("description", "")),
            publicada_en=self._fecha(bruto.get("publication_date")),
            tags=bruto.get("tags") or [],
        )

    @staticmethod
    def _fecha(valor: str | None) -> datetime | None:
        if not valor:
            return None
        try:
            return datetime.fromisoformat(valor)
        except ValueError:
            return None

    @staticmethod
    def _filtra(ofertas: list[RawJob], query: SearchQuery) -> list[RawJob]:
        terminos = normaliza(query.texto).split()
        if not terminos:
            return ofertas

        resultado = []
        for oferta in ofertas:
            heno = normaliza(
                f"{oferta.titulo} {oferta.descripcion} {' '.join(oferta.tags)}"
            )
            if any(t in heno for t in terminos):
                resultado.append(oferta)
        return resultado
