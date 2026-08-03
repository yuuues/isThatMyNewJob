from pydantic import BaseModel


class FakeProvider:
    """Provider determinista. Cicla por las respuestas programadas."""

    nombre = "fake"

    def __init__(
        self, respuestas: list[BaseModel], error: Exception | None = None
    ) -> None:
        self.respuestas = respuestas
        self.error = error
        self.llamadas: list[dict] = []
        self._indice = 0

    def complete_json(self, *, system: str, user: str, modelo_salida: type[BaseModel]):
        self.llamadas.append({"system": system, "user": user, "modelo": modelo_salida})
        if self.error is not None:
            raise self.error
        if not self.respuestas:
            raise AssertionError("FakeProvider sin respuestas programadas")

        respuesta = self.respuestas[self._indice % len(self.respuestas)]
        self._indice += 1
        return respuesta
