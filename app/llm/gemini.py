from google import genai
from google.genai import types
from pydantic import BaseModel


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
        if respuesta.parsed is None:
            raise ValueError(
                f"Gemini no devolvió JSON válido para {modelo_salida.__name__}: "
                f"{(respuesta.text or '')[:200]}"
            )
        return respuesta.parsed
