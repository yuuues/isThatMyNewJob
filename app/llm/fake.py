from pydantic import BaseModel


class FakeProvider:
    """Provider determinista. Cicla por las respuestas programadas.

    `error` sin más se lanza en todas las llamadas. Con `fallos=N` se lanza sólo en
    las N primeras y a partir de ahí se devuelven las respuestas: así se prueban los
    reintentos ante un fallo transitorio sin tocar la red ni el reloj.
    """

    nombre = "fake"

    def __init__(
        self,
        respuestas: list[BaseModel],
        error: Exception | None = None,
        *,
        fallos: int | None = None,
    ) -> None:
        self.respuestas = respuestas
        self.error = error
        self.fallos = fallos
        self.llamadas: list[dict] = []
        self._indice = 0

    def _toca_fallar(self) -> bool:
        if self.error is None:
            return False
        if self.fallos is None:
            return True
        return len(self.llamadas) <= self.fallos

    def complete_json(self, *, system: str, user: str, modelo_salida: type[BaseModel]):
        self.llamadas.append({"system": system, "user": user, "modelo": modelo_salida})
        if self._toca_fallar():
            raise self.error
        if not self.respuestas:
            raise AssertionError("FakeProvider sin respuestas programadas")

        respuesta = self.respuestas[self._indice % len(self.respuestas)]
        self._indice += 1
        return respuesta
