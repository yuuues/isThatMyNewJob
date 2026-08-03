import re
from datetime import datetime

import httpx

from app.limitador import LimitadorPorHost
from app.presupuesto import Presupuesto, SinLimite
from app.schemas import Modalidad, RawJob, SearchQuery
from app.sources.base import FuenteConFiltroEnServidor
from app.sources.comun import detecta_modalidad
from app.sources.remotive import html_a_texto

URL_API = "https://scrappa.co/api/indeed/jobs"

# Un crédito por llamada, no por oferta: cada llamada devuelve hasta 20. Con 500
# créditos gratis al mes salen unas 10.000 ofertas, el mejor ratio de las cinco fuentes.
RESULTADOS_POR_LLAMADA = 20

_PAISES = {"es": "Spain", "pt": "Portugal", "fr": "France", "de": "Germany",
           "gb": "United Kingdom", "us": "United States", "it": "Italy", "nl": "Netherlands"}

# Etiquetas de `attributes` que hablan de modalidad. Se comprueba híbrido primero por
# el mismo motivo que en app/sources/comun.py: una oferta híbrida menciona el
# teletrabajo de los días que toca, y sin ese orden pasaría por remota total.
_ATRIBUTO_HIBRIDO = {"hybrid work", "hybrid"}
_ATRIBUTO_REMOTO = {"remote", "remote work", "fully remote"}


class ScrappaSource(FuenteConFiltroEnServidor):
    """Ofertas de Indeed servidas por Scrappa, que mantiene la relación con la fuente.

    Es la fuente con mejor material del proyecto para España. Medido sobre 20 ofertas
    reales: descripciones de 856 a 8914 caracteres, mediana 3285, ninguna truncada.
    Adzuna corta a 500 y JSearch tiene mediana 1831.

    Trae además dos señales que las otras fuentes obligan a adivinar: `is_remote` como
    booleano y `attributes` con la modalidad y las tecnologías ya etiquetadas.
    """

    nombre = "scrappa"

    def __init__(
        self,
        api_key: str,
        timeout: float = 60.0,
        limitador: LimitadorPorHost | None = None,
        presupuesto: Presupuesto | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Scrappa necesita SCRAPPA_API_KEY")
        self.api_key = api_key
        self._timeout = timeout
        self._limitador = limitador or LimitadorPorHost()
        self._presupuesto = presupuesto or SinLimite()

    def search(self, query: SearchQuery) -> list[RawJob]:
        # El cupo se reserva antes de llamar: si no queda, no se gasta una petición
        # para descubrirlo. Un fallo posterior no lo devuelve, porque el proveedor ya
        # habrá descontado el crédito.
        self._presupuesto.exige(1)

        self._limitador.espera_turno(URL_API)
        respuesta = httpx.get(
            URL_API,
            params={
                "query": query.texto,
                "location": query.ubicacion or _PAISES.get(query.pais, query.pais),
                "country": query.pais,
            },
            headers={"X-API-KEY": self.api_key, "Accept": "application/json"},
            timeout=self._timeout,
        )
        datos = self._json_o_error(respuesta)
        if not datos.get("success"):
            return []

        jobs = (datos.get("data") or {}).get("jobs") or []
        return [self._normaliza(j) for j in jobs][: query.max_resultados]

    @staticmethod
    def _json_o_error(respuesta: httpx.Response) -> dict:
        try:
            respuesta.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Scrappa respondió {respuesta.status_code}: {respuesta.text[:200]}"
            ) from e
        try:
            return respuesta.json()
        except ValueError:
            raise RuntimeError(
                f"Scrappa devolvió contenido no-JSON: {respuesta.text[:200]}"
            ) from None

    def _normaliza(self, bruto: dict) -> RawJob:
        titulo = bruto.get("title") or ""
        descripcion = html_a_texto(bruto.get("description_html") or "")
        atributos = bruto.get("attributes") or []
        ubicacion = bruto.get("location") or {}

        return RawJob(
            fuente=self.nombre,
            external_id=str(bruto.get("id") or ""),
            url=bruto.get("apply_url") or "",
            titulo=titulo,
            empresa=(bruto.get("company") or {}).get("name") or "Desconocida",
            ubicacion=ubicacion.get("formatted") or ubicacion.get("city"),
            modalidad=self._modalidad(ubicacion, atributos, titulo, descripcion),
            salario_texto=self._salario(bruto.get("salary")),
            descripcion=descripcion,
            publicada_en=self._fecha(bruto.get("date_published")),
            tags=atributos,
        )

    @staticmethod
    def _modalidad(ubicacion: dict, atributos: list, titulo: str, descripcion: str) -> Modalidad:
        """Combina las tres señales, de la más fiable a la más débil.

        Híbrido se mira antes que remoto en los dos primeros pasos: una oferta puede
        traer "Remote" en `attributes` y ser híbrida en realidad, como se ve en los
        datos reales, donde una oferta con esa etiqueta dice "modalidad remota parcial"
        en la descripción.
        """
        etiquetas = {str(a).lower() for a in atributos}
        if etiquetas & _ATRIBUTO_HIBRIDO:
            return "hibrido"

        por_texto = detecta_modalidad(f"{titulo} {descripcion}")
        if por_texto == "hibrido":
            return "hibrido"

        if ubicacion.get("is_remote") is True or etiquetas & _ATRIBUTO_REMOTO:
            return "remoto"
        return por_texto

    @staticmethod
    def _salario(valor) -> str | None:
        """El salario viaja como texto, nunca como número.

        Medido: `salary` llega null en las 20 ofertas españolas de la muestra, así que
        no conocemos la forma de uno no nulo ni su periodo. Meterlo en los campos
        numéricos permitiría compararlo contra el mínimo anual del prefiltro, que es
        exactamente el error que descartaba ofertas de Adzuna a 48-60 €/hora.
        """
        if valor is None or valor == "":
            return None
        return valor if isinstance(valor, str) else str(valor)

    @staticmethod
    def _fecha(valor: str | None) -> datetime | None:
        if not valor:
            return None
        try:
            return datetime.fromisoformat(re.sub(r"Z$", "", valor.strip()))
        except ValueError:
            return None
