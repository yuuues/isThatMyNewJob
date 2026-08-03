from typing import Protocol

from app.schemas import RawJob, SearchQuery


class JobSource(Protocol):
    """Una fuente de ofertas.

    Ojo: no todas las fuentes filtran en servidor. Remotive y Arbeitnow devuelven
    un feed completo e ignoran el texto de búsqueda, así que su implementación
    filtra en local. Quien consume esta interfaz no debe asumir filtrado remoto.
    """

    nombre: str

    def search(self, query: SearchQuery) -> list[RawJob]: ...
