from app.schemas import RawJob, SearchQuery


class FakeSource:
    """Fuente en memoria. Permite probar el pipeline completo sin red.

    `filtra_en_servidor` se puede invertir para imitar a Remotive o Arbeitnow: en ese
    caso una sola "descarga" atiende a todas las búsquedas. La fake no filtra por
    texto —devuelve siempre lo que se le cargó— porque lo que se prueba con ella es la
    orquestación, no el filtro; el filtro local se prueba contra las fuentes reales.
    """

    def __init__(
        self,
        ofertas: list[RawJob],
        nombre: str = "fake",
        error: Exception | None = None,
        filtra_en_servidor: bool = True,
    ) -> None:
        self.ofertas = ofertas
        self.nombre = nombre
        self.error = error
        self.filtra_en_servidor = filtra_en_servidor
        self.llamadas: list[SearchQuery] = []
        self.descargas = 0

    def search(self, query: SearchQuery) -> list[RawJob]:
        self.llamadas.append(query)
        return self._entrega()

    def busca_varias(self, queries: list[SearchQuery]) -> list[RawJob]:
        if self.filtra_en_servidor:
            ofertas: list[RawJob] = []
            for query in queries:
                ofertas.extend(self.search(query))
            return ofertas

        self.llamadas.extend(queries)
        return self._entrega()

    def _entrega(self) -> list[RawJob]:
        self.descargas += 1
        if self.error is not None:
            raise self.error
        return list(self.ofertas)
