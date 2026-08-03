"""Utilidades compartidas por varios conectores."""

from app.dedup import normaliza
from app.schemas import Modalidad

_PALABRAS_REMOTO = ("remoto", "teletrabajo", "remote", "full remote", "en remoto")
_PALABRAS_HIBRIDO = ("hibrido", "híbrido", "hybrid", "semipresencial")


def detecta_modalidad(texto: str) -> Modalidad:
    """Infiere la modalidad del texto de la oferta.

    Ni Adzuna ni JSearch la publican de forma fiable: Adzuna no trae el campo, y en
    JSearch `job_is_remote` llega en `false` incluso en ofertas cuyo título dice
    "Remoto" (medido sobre datos reales del mercado español).

    Se comprueba híbrido antes que remoto: una oferta híbrida menciona el teletrabajo
    de los días que toca, y sin ese orden se clasificaría como totalmente remota.
    """
    normalizado = normaliza(texto)
    if any(normaliza(p) in normalizado for p in _PALABRAS_HIBRIDO):
        return "hibrido"
    if any(normaliza(p) in normalizado for p in _PALABRAS_REMOTO):
        return "remoto"
    return "desconocida"
