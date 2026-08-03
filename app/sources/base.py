from typing import Protocol

from app.schemas import RawJob, SearchQuery


class JobSource(Protocol):
    """Una fuente de ofertas.

    No todas las fuentes filtran en servidor: Remotive y Arbeitnow devuelven un feed
    completo e ignoran el texto de búsqueda. Eso no puede quedar en una convención
    implícita, porque decide cuántas peticiones hace la ingesta: por eso cada fuente
    declara `filtra_en_servidor` y expone `busca_varias()`.

    - `filtra_en_servidor = True`  -> una petición por búsqueda; el servidor filtra.
    - `filtra_en_servidor = False` -> una sola descarga por run; el filtro es local
      y se aplica contra todas las búsquedas.
    """

    nombre: str
    filtra_en_servidor: bool

    def search(self, query: SearchQuery) -> list[RawJob]: ...

    def busca_varias(self, queries: list[SearchQuery]) -> list[RawJob]: ...


def _sin_repetidas(ofertas: list[RawJob]) -> list[RawJob]:
    """Une resultados de varias búsquedas conservando el orden y sin repetir.

    La deduplicación fina es cosa de `ingest`; aquí sólo se evita devolver dos veces
    la misma oferta cuando encaja con más de una búsqueda."""
    vistas: set[str] = set()
    resultado: list[RawJob] = []
    for oferta in ofertas:
        if oferta.external_id in vistas:
            continue
        vistas.add(oferta.external_id)
        resultado.append(oferta)
    return resultado


class FuenteConFiltroEnServidor:
    """Base para fuentes que aplican la búsqueda en su API (Adzuna)."""

    filtra_en_servidor = True

    def search(self, query: SearchQuery) -> list[RawJob]:  # pragma: no cover - lo implementa la fuente
        raise NotImplementedError

    def busca_varias(self, queries: list[SearchQuery]) -> list[RawJob]:
        ofertas: list[RawJob] = []
        for query in queries:
            ofertas.extend(self.search(query))
        return _sin_repetidas(ofertas)


class FuenteFiltradaEnLocal:
    """Base para fuentes cuya API devuelve el feed entero e ignora el texto buscado.

    El feed se descarga una sola vez y se filtra en local contra cada búsqueda: con
    tres búsquedas guardadas, tres descargas idénticas serían un abuso de la API
    ajena (Remotive pide ~4 peticiones al día) sin aportar ni una oferta más.
    """

    filtra_en_servidor = False

    def search(self, query: SearchQuery) -> list[RawJob]:
        return self._aplica_query(self._descarga_feed(), query)

    def busca_varias(self, queries: list[SearchQuery]) -> list[RawJob]:
        if not queries:
            return []
        feed = self._descarga_feed()
        ofertas: list[RawJob] = []
        for query in queries:
            ofertas.extend(self._aplica_query(feed, query))
        return _sin_repetidas(ofertas)

    def _descarga_feed(self) -> list[RawJob]:  # pragma: no cover - lo implementa la fuente
        raise NotImplementedError

    def _aplica_query(
        self, ofertas: list[RawJob], query: SearchQuery
    ) -> list[RawJob]:  # pragma: no cover - lo implementa la fuente
        raise NotImplementedError
