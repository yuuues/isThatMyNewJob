from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ErrorProveedorLLM(RuntimeError):
    """Fallo del proveedor expresado en el vocabulario del dominio.

    Existe para que el pipeline no tenga que inspeccionar excepciones de httpx ni del
    SDK de Gemini: cada implementación traduce lo suyo a esta jerarquía.
    """


class CuotaAgotadaError(ErrorProveedorLLM):
    """El proveedor rechaza la petición por cuota agotada o rate limit.

    No es un fallo transitorio: reintentarlo sólo gasta lo que ya no queda. Ante esta
    señal el pipeline corta las llamadas, cierra el run y deja la cola para mañana.
    """


class LLMProvider(Protocol):
    """Un modelo capaz de devolver JSON conforme a un esquema Pydantic.

    La extracción del CV desde PDF NO pasa por aquí: es multimodal y DeepSeek no la
    soporta. Vive en app/profile.py, hablando con Gemini directamente.

    Contrato de errores: ante cuota agotada o rate limit se levanta
    `CuotaAgotadaError`. Cualquier otro fallo se propaga tal cual y el pipeline lo
    trata como transitorio (lo reintenta).
    """

    nombre: str

    def complete_json(self, *, system: str, user: str, modelo_salida: type[T]) -> T: ...
