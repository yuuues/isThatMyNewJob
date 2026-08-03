from datetime import datetime

import httpx

from app.limitador import LimitadorPorHost
from app.schemas import RawJob, SearchQuery
from app.sources.base import FuenteConFiltroEnServidor
from app.sources.comun import detecta_modalidad, salario_anual

# Verificado contra la API real el 2026-08-03: las descripciones llegan cortadas a
# 500 caracteres. No es configurable ni hay un campo con el texto completo.
_LIMITE_DESCRIPCION = 500


def url_api(pais: str) -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{pais}/search/1"


class AdzunaSource(FuenteConFiltroEnServidor):
    """Agregador con cobertura de España. Filtra en servidor vía `what` y `where`,
    así que necesita una petición por búsqueda guardada."""

    nombre = "adzuna"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        timeout: float = 30.0,
        limitador: LimitadorPorHost | None = None,
    ) -> None:
        if not app_id or not app_key:
            raise ValueError("Adzuna necesita credenciales: ADZUNA_APP_ID y ADZUNA_APP_KEY")
        self.app_id = app_id
        self.app_key = app_key
        self._timeout = timeout
        self._limitador = limitador or LimitadorPorHost()

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

        url = url_api(query.pais)
        self._limitador.espera_turno(url)
        respuesta = httpx.get(url, params=params, timeout=self._timeout)
        datos = self._json_o_error(respuesta)
        return [self._normaliza(r) for r in datos.get("results", [])]

    @staticmethod
    def _json_o_error(respuesta: httpx.Response) -> dict:
        """Convierte cualquier fallo de Adzuna en un error legible.

        El código de estado se mira primero: un 401 o un 429 traen cuerpo JSON válido
        y sin `raise_for_status()` se colaban como respuesta buena, devolviendo cero
        ofertas en silencio. Los errores con cuerpo HTML se cubren después, para que
        el fallo no aparezca como un JSONDecodeError críptico.

        Decisión: se envuelve en RuntimeError en vez de propagar `HTTPStatusError`
        porque el cuerpo de Adzuna lleva el motivo real y el mensaje lo conserva.
        """
        try:
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Adzuna respondió {respuesta.status_code}: {respuesta.text[:200]}"
            ) from e

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
        salario_min, salario_max, salario_texto = self._salario_publicado(bruto)
        return RawJob(
            fuente=self.nombre,
            external_id=str(bruto["id"]),
            url=bruto.get("redirect_url", ""),
            titulo=titulo,
            empresa=(bruto.get("company") or {}).get("display_name", "Desconocida"),
            ubicacion=(bruto.get("location") or {}).get("display_name"),
            modalidad=detecta_modalidad(f"{titulo} {descripcion}"),
            salario_min=salario_min,
            salario_max=salario_max,
            salario_texto=salario_texto,
            descripcion=descripcion,
            descripcion_truncada=self._esta_truncada(descripcion),
            publicada_en=self._fecha(bruto.get("created")),
        )

    @staticmethod
    def _salario_publicado(bruto: dict) -> tuple[float | None, float | None, str | None]:
        """Descarta los salarios que Adzuna estima en vez de publicar.

        `salary_is_predicted` llega como cadena "0"/"1". Cuando vale "1" la cifra es
        una predicción del propio Adzuna, no un dato de la oferta. Tratarla como
        publicada rompe la regla del prompt de no estimar salarios, y peor: el
        prefiltro descartaría ofertas por un sueldo que nadie llegó a ofrecer.

        Medido sobre 50 ofertas reales: sólo 4 traen salario de cualquier tipo.

        Adzuna tampoco publica el periodo, así que las cifras que no pueden ser anuales
        (tarifas por hora) salen como texto y no como número: ver salario_anual().
        """
        if str(bruto.get("salary_is_predicted", "0")) == "1":
            return None, None, None
        return salario_anual(bruto.get("salary_min"), bruto.get("salary_max"))

    @staticmethod
    def _esta_truncada(descripcion: str) -> bool:
        """Adzuna corta a 500 caracteres y remata con puntos suspensivos.

        Se comprueban las dos señales por separado: el límite podría cambiar, y una
        descripción corta también puede venir cortada si el original lo estaba.
        """
        return descripcion.endswith("…") or len(descripcion) >= _LIMITE_DESCRIPCION

    @staticmethod
    def _fecha(valor: str | None) -> datetime | None:
        if not valor:
            return None
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
