from app.schemas import RawJob, SearchQuery


class FakeSource:
    """Fuente en memoria. Permite probar el pipeline completo sin red."""

    def __init__(
        self,
        ofertas: list[RawJob],
        nombre: str = "fake",
        error: Exception | None = None,
    ) -> None:
        self.ofertas = ofertas
        self.nombre = nombre
        self.error = error
        self.llamadas: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[RawJob]:
        self.llamadas.append(query)
        if self.error is not None:
            raise self.error
        return list(self.ofertas)
