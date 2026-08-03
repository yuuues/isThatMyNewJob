from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    """Un modelo capaz de devolver JSON conforme a un esquema Pydantic.

    La extracción del CV desde PDF NO pasa por aquí: es multimodal y DeepSeek no la
    soporta. Vive en app/profile.py, hablando con Gemini directamente.
    """

    nombre: str

    def complete_json(self, *, system: str, user: str, modelo_salida: type[T]) -> T: ...
