from datetime import datetime

import httpx

from app.dedup import normaliza
from app.schemas import Modalidad, RawJob, SearchQuery

_PALABRAS_REMOTO = ("remoto", "teletrabajo", "remote", "full remote", "en remoto")
_PALABRAS_HIBRIDO = ("hibrido", "híbrido", "hybrid", "semipresencial")


def url_api(pais: str) -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{pais}/search/1"


def detecta_modalidad(texto: str) -> Modalidad:
    """Adzuna no publica la modalidad como campo, así que se infiere del texto."""
    normalizado = normaliza(texto)
    if any(normaliza(p) in normalizado for p in _PALABRAS_HIBRIDO):
        return "hibrido"
    if any(normaliza(p) in normalizado for p in _PALABRAS_REMOTO):
        return "remoto"
    return "desconocida"


class AdzunaSource:
    """Agregador con cobertura de España. Filtra en servidor vía `what` y `where`."""

    nombre = "adzuna"

    def __init__(self, app_id: str, app_key: str, timeout: float = 30.0) -> None:
        if not app_id or not app_key:
            raise ValueError("Adzuna necesita credenciales: ADZUNA_APP_ID y ADZUNA_APP_KEY")
        self.app_id = app_id
        self.app_key = app_key
        self._timeout = timeout

    def search(self, query: SearchQuery) -> list[RawJob]:
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": query.texto,
            "results_per_page": min(query.max_resultados, 50),
            "content-type": "application/json",
        }
        if query.ubicacion:
            params["where"] = query.ubicacion

        respuesta = httpx.get(url_api(query.pais), params=params, timeout=self._timeout)
        datos = self._json_o_error(respuesta)
        return [self._normaliza(r) for r in datos.get("results", [])]

    @staticmethod
    def _json_o_error(respuesta: httpx.Response) -> dict:
        """Adzuna devuelve HTML en los errores, no JSON. Sin esto el fallo sería un
        JSONDecodeError críptico en vez del código de estado real."""
        try:
            return respuesta.json()
        except ValueError:
            raise RuntimeError(
                f"Adzuna respondió {respuesta.status_code} con contenido no-JSON: "
                f"{respuesta.text[:200]}"
            ) from None

    def _normaliza(self, bruto: dict) -> RawJob:
        descripcion = bruto.get("description", "")
        titulo = bruto.get("title", "")
        return RawJob(
            fuente=self.nombre,
            external_id=str(bruto["id"]),
            url=bruto.get("redirect_url", ""),
            titulo=titulo,
            empresa=(bruto.get("company") or {}).get("display_name", "Desconocida"),
            ubicacion=(bruto.get("location") or {}).get("display_name"),
            modalidad=detecta_modalidad(f"{titulo} {descripcion}"),
            salario_min=bruto.get("salary_min"),
            salario_max=bruto.get("salary_max"),
            descripcion=descripcion,
            publicada_en=self._fecha(bruto.get("created")),
        )

    @staticmethod
    def _fecha(valor: str | None) -> datetime | None:
        if not valor:
            return None
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
