import json

import httpx
from pydantic import BaseModel, ValidationError

from app.llm.base import CuotaAgotadaError

URL_API = "https://api.deepseek.com/chat/completions"

# 429: rate limit. 402: saldo agotado — DeepSeek es de prepago, así que quedarse sin
# saldo es exactamente "cuota agotada" y reintentar no lo va a arreglar.
CODIGOS_CUOTA = (402, 429)


class DeepSeekProvider:
    """API compatible con OpenAI. Se usa httpx directamente para no arrastrar el SDK.

    El modo de pensamiento viene activado por defecto en DeepSeek y aquí se desactiva.
    Medido sobre una clasificación real con deepseek-v4-pro: 62 segundos y 1929 tokens
    de salida, de los cuales 1612 eran razonamiento. A 179 ofertas por run eso son tres
    horas y el 84% del gasto de salida, para una tarea que ya lleva sus criterios
    escritos en el prompt.

    Hay una razón de corrección además del coste: la documentación de DeepSeek dice que
    en modo pensamiento `temperature` se ignora en silencio, sin dar error. El diseño
    exige temperatura 0.2 para que la clasificación sea consistente, así que con el
    pensamiento activo esa garantía no existía.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str = "deepseek-v4-flash",
        timeout: float = 120.0,
        pensamiento: bool = False,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek necesita DEEPSEEK_API_KEY")
        self.api_key = api_key
        self.modelo = modelo
        self.nombre = modelo
        self._timeout = timeout
        self._pensamiento = pensamiento

    def complete_json(self, *, system: str, user: str, modelo_salida: type[BaseModel]):
        esquema = json.dumps(modelo_salida.model_json_schema(), ensure_ascii=False)
        instrucciones = (
            f"{system}\n\n"
            f"Responde ÚNICAMENTE con un objeto JSON que cumpla este esquema:\n{esquema}"
        )

        respuesta = httpx.post(
            URL_API,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.modelo,
                "temperature": 0.2,
                "thinking": {"type": "enabled" if self._pensamiento else "disabled"},
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instrucciones},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self._timeout,
        )
        if respuesta.status_code in CODIGOS_CUOTA:
            raise CuotaAgotadaError(
                f"DeepSeek rechaza la petición por cuota (HTTP {respuesta.status_code}): "
                f"{respuesta.text[:200]}"
            )
        respuesta.raise_for_status()

        contenido = respuesta.json()["choices"][0]["message"]["content"]
        try:
            return modelo_salida.model_validate_json(contenido)
        except (ValidationError, ValueError) as e:
            raise ValueError(
                f"DeepSeek no devolvió JSON válido para {modelo_salida.__name__}: {contenido[:200]}"
            ) from e
