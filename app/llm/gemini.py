from google import genai
from google.genai import types
from pydantic import BaseModel

from app.llm.base import CuotaAgotadaError

# Señales de cuota agotada en la API de Gemini. El SDK expone el código HTTP en
# `code`/`status_code` según la versión, y el cuerpo trae RESOURCE_EXHAUSTED. Se mira
# también el texto porque la forma exacta de la excepción cambia entre versiones del
# SDK y quedarse corto aquí significaría vaciar la cuota a fuerza de reintentos.
TEXTOS_CUOTA = ("RESOURCE_EXHAUSTED", "QUOTA", "RATE LIMIT", "TOO MANY REQUESTS")


def _es_cuota_agotada(e: Exception) -> bool:
    codigo = getattr(e, "code", None) or getattr(e, "status_code", None)
    if codigo == 429:
        return True
    texto = str(e).upper()
    return any(senal in texto for senal in TEXTOS_CUOTA)


class GeminiProvider:
    def __init__(
        self, api_key: str, modelo: str = "gemini-2.5-flash", temperatura: float = 0.2
    ) -> None:
        if not api_key:
            raise ValueError("Gemini necesita GEMINI_API_KEY")
        self.cliente = genai.Client(api_key=api_key)
        self.modelo = modelo
        self.nombre = modelo
        self.temperatura = temperatura

    def complete_json(self, *, system: str, user: str, modelo_salida: type[BaseModel]):
        try:
            respuesta = self.cliente.models.generate_content(
                model=self.modelo,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    temperature=self.temperatura,
                    response_mime_type="application/json",
                    response_schema=modelo_salida,
                ),
            )
        except Exception as e:
            if _es_cuota_agotada(e):
                raise CuotaAgotadaError(f"Gemini indica cuota agotada: {e}") from e
            raise

        if respuesta.parsed is None:
            raise ValueError(
                f"Gemini no devolvió JSON válido para {modelo_salida.__name__}: "
                f"{(respuesta.text or '')[:200]}"
            )
        return respuesta.parsed
