import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from app.limitador import LimitadorPorHost
from app.presupuesto import Presupuesto, SinLimite
from app.schemas import RawJob, SearchQuery
from app.sources.base import FuenteConFiltroEnServidor
from app.sources.comun import detecta_modalidad

URL_API = "https://jsearch.p.rapidapi.com/search-v2"
_HOST = "jsearch.p.rapidapi.com"

# La API cobra un crédito por página de 10 resultados, no por petición HTTP.
RESULTADOS_POR_PAGINA = 10

_PAISES = {"es": "Spain", "gb": "United Kingdom", "us": "United States", "de": "Germany",
           "fr": "France", "pt": "Portugal", "nl": "Netherlands", "it": "Italy"}

_UNIDADES = {
    "minuto": timedelta(minutes=1),
    "hora": timedelta(hours=1),
    "dia": timedelta(days=1),
    "semana": timedelta(weeks=1),
    "mes": timedelta(days=30),
    "ano": timedelta(days=365),
}
_RELATIVA = re.compile(r"hace\s+(\d+)\s+([a-záéíóúñ]+)", re.IGNORECASE)


def limpia_ubicacion(bruta: str | None) -> str | None:
    """Quita el publisher que JSearch pega a la ubicación.

    Llega como 'Madrid     •  a través de LinkedIn'. Sin limpiarlo, el nombre del
    portal entra en la clave de deduplicación y la misma oferta servida por dos
    publishers dejaría de deduplicar.
    """
    if not bruta:
        return None
    return re.sub(r"\s+", " ", bruta.split("•")[0]).strip() or None


def fecha_relativa(texto: str | None, ahora: datetime) -> datetime | None:
    """Convierte 'hace 5 días' en una fecha.

    `job_posted_at_datetime_utc` llega nulo en el 100% de la muestra española, así
    que la única señal de frescura es este texto, ya localizado por el parámetro
    `language`. Lo que no encaje con el patrón devuelve None: para una herramienta
    de búsqueda de empleo, no saber la fecha es mejor que inventarla.
    """
    if not texto:
        return None
    encaje = _RELATIVA.search(texto)
    if not encaje:
        return None

    cantidad = int(encaje.group(1))
    unidad = encaje.group(2).lower().rstrip("s")
    unidad = unidad.replace("í", "i").replace("ñ", "n").replace("é", "e")
    delta = _UNIDADES.get(unidad)
    return ahora - cantidad * delta if delta else None


class JSearchSource(FuenteConFiltroEnServidor):
    """Google for Jobs vía RapidAPI: agrega LinkedIn, Indeed, Glassdoor y otros.

    Es la única fuente con descripción completa para España (medido: mediana de 1831
    caracteres frente a los 500 exactos de Adzuna), y también la única con cupo
    mensual de límite duro. Por eso lleva presupuesto: sin él se agota a mitad de mes
    y deja de traer ofertas sin avisar.
    """

    nombre = "jsearch"

    def __init__(
        self,
        api_key: str,
        paginas: int = 1,
        timeout: float = 45.0,
        limitador: LimitadorPorHost | None = None,
        presupuesto: Presupuesto | None = None,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("JSearch necesita JSEARCH_API_KEY")
        self.api_key = api_key
        self.paginas = paginas
        self._timeout = timeout
        self._limitador = limitador or LimitadorPorHost()
        self._presupuesto = presupuesto or SinLimite()
        self._reloj = reloj or (lambda: datetime.now(UTC).replace(tzinfo=None))

    def search(self, query: SearchQuery) -> list[RawJob]:
        # El cupo se reserva antes de llamar: si no queda, no se gasta una petición
        # para descubrirlo. Un fallo posterior no lo devuelve, porque el proveedor
        # ya habrá descontado el crédito.
        self._presupuesto.exige(self.paginas)

        self._limitador.espera_turno(URL_API)
        respuesta = httpx.get(
            URL_API,
            params=self._params(query),
            headers={"X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": _HOST},
            timeout=self._timeout,
        )
        datos = self._json_o_error(respuesta)
        jobs = (datos.get("data") or {}).get("jobs") or []
        ahora = self._reloj()
        return [self._normaliza(j, ahora) for j in jobs][: query.max_resultados]

    def _params(self, query: SearchQuery) -> dict:
        lugar = query.ubicacion or _PAISES.get(query.pais, query.pais)
        params = {
            "query": f"{query.texto} in {lugar}".strip(),
            "country": query.pais,
            "language": "es" if query.pais == "es" else "en",
            "num_pages": self.paginas,
        }
        if query.solo_remoto:
            params["work_from_home"] = "true"
        return params

    @staticmethod
    def _json_o_error(respuesta: httpx.Response) -> dict:
        if respuesta.status_code == 404:
            raise RuntimeError(
                "JSearch respondió 404: el endpoint no existe. La API cambió de ruta "
                f"(no es un problema de credenciales). Cuerpo: {respuesta.text[:200]}"
            )
        try:
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"JSearch respondió {respuesta.status_code}: {respuesta.text[:200]}"
            ) from e
        try:
            return respuesta.json()
        except ValueError:
            raise RuntimeError(
                f"JSearch devolvió contenido no-JSON: {respuesta.text[:200]}"
            ) from None

    def _normaliza(self, bruto: dict, ahora: datetime) -> RawJob:
        titulo = bruto.get("job_title") or ""
        descripcion = bruto.get("job_description") or ""
        salario_min, salario_max, salario_texto = self._salario(bruto)

        return RawJob(
            fuente=self.nombre,
            external_id=str(bruto.get("job_id") or bruto.get("job_uid") or ""),
            url=bruto.get("job_apply_link") or "",
            titulo=titulo,
            empresa=bruto.get("employer_name") or "Desconocida",
            ubicacion=limpia_ubicacion(bruto.get("job_location")),
            modalidad=self._modalidad(bruto, titulo, descripcion),
            salario_min=salario_min,
            salario_max=salario_max,
            salario_texto=salario_texto,
            descripcion=descripcion,
            publicada_en=fecha_relativa(bruto.get("job_posted_at"), ahora),
            tags=bruto.get("job_employment_types") or [],
        )

    @staticmethod
    def _modalidad(bruto: dict, titulo: str, descripcion: str):
        """`job_is_remote=True` se cree; `False` no.

        Medido sobre datos reales: una oferta titulada "Desarrollador PHP/Laravel
        Senior — Remoto" llega con el campo en false. Un false sólo significa que la
        API no lo ha detectado, así que se recurre al texto.
        """
        if bruto.get("job_is_remote") is True:
            return "remoto"
        return detecta_modalidad(f"{titulo} {descripcion}")

    @staticmethod
    def _salario(bruto: dict) -> tuple[float | None, float | None, str | None]:
        """Sólo se aceptan cifras anuales como números.

        `job_salary_period` puede ser HOUR o MONTH. Comparar 25 €/hora contra un
        mínimo anual de 45.000 descartaría la oferta por bien pagada que estuviera,
        así que lo no anual viaja como texto para que lo lea el modelo.
        """
        minimo, maximo = bruto.get("job_min_salary"), bruto.get("job_max_salary")
        if minimo is None and maximo is None:
            return None, None, None

        periodo = bruto.get("job_salary_period")
        if periodo == "YEAR":
            return minimo, maximo, None
        return None, None, f"{minimo} - {maximo} por {periodo or 'periodo no indicado'}"
