"""Utilidades compartidas por varios conectores."""

from app.dedup import normaliza
from app.schemas import Modalidad

_PALABRAS_REMOTO = ("remoto", "teletrabajo", "remote", "full remote", "en remoto")
_PALABRAS_HIBRIDO = ("hibrido", "híbrido", "hybrid", "semipresencial")


# Por debajo de esto, una cifra no puede ser un salario anual en el mercado español:
# es una tarifa por hora o por día. Medido en datos reales de Adzuna, que no publica el
# periodo: ofertas de "48 - 60" para un Senior Laravel Developer.
SALARIO_ANUAL_MINIMO_PLAUSIBLE = 5000


def salario_anual(
    minimo: float | None, maximo: float | None
) -> tuple[float | None, float | None, str | None]:
    """Separa las cifras que pueden ser un salario anual de las que no.

    Una fuente sin campo de periodo puede dar 60 queriendo decir 60 €/hora. Tratarlo
    como anual hace que el prefiltro descarte por sueldo bajo justo las ofertas mejor
    pagadas. Lo dudoso sale como texto: no se filtra por ello, pero el modelo lo ve y
    puede razonar sobre ello.
    """
    if minimo is None and maximo is None:
        return None, None, None

    referencia = maximo if maximo is not None else minimo
    if referencia is not None and referencia < SALARIO_ANUAL_MINIMO_PLAUSIBLE:
        return None, None, f"{minimo} - {maximo} (la fuente no indica el periodo)"
    return minimo, maximo, None


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
