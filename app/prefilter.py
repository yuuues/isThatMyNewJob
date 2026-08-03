from dataclasses import dataclass

from app.dedup import normaliza
from app.schemas import Preferencias, RawJob

_STOPWORDS = {
    "es": {
        "de", "la", "el", "que", "y", "en", "los", "del", "las", "para", "con", "por",
        "una", "un", "como", "mas", "este", "esta", "se", "su", "al", "lo", "nuestro",
        "experiencia", "empresa", "equipo", "buscamos",
    },
    "en": {
        "the", "and", "of", "to", "in", "a", "for", "with", "you", "we", "our", "is",
        "are", "will", "your", "as", "on", "at", "be", "have", "team", "looking",
    },
    "de": {
        "und", "der", "die", "das", "ein", "eine", "mit", "fur", "von", "zu", "im",
        "den", "dem", "ist", "sie", "wir", "auf", "als", "des", "sind", "suchen",
    },
}

_MINIMO_PALABRAS = 8
_MINIMO_ACIERTOS = 3


@dataclass(frozen=True)
class ResultadoPrefiltro:
    descartada: bool
    motivo: str | None = None


def detecta_idioma(texto: str) -> str:
    """Heurística de stopwords. Suficiente para separar es/en/de, que es lo que recibimos.

    Devuelve 'desconocido' cuando el texto es demasiado corto o no hay señal clara,
    y en ese caso el prefiltro no descarta: ante la duda, que decida el LLM.
    """
    palabras = normaliza(texto).split()
    if len(palabras) < _MINIMO_PALABRAS:
        return "desconocido"

    conjunto = set(palabras)
    aciertos = {idioma: len(conjunto & stops) for idioma, stops in _STOPWORDS.items()}
    mejor = max(aciertos, key=lambda k: aciertos[k])

    if aciertos[mejor] < _MINIMO_ACIERTOS:
        return "desconocido"
    return mejor


def aplica_prefiltro(job: RawJob, prefs: Preferencias) -> ResultadoPrefiltro:
    """Reglas deterministas previas al LLM. Ante la duda, no descarta."""
    texto = f"{job.titulo}\n{job.descripcion}"

    idioma = detecta_idioma(texto)
    if idioma != "desconocido" and idioma not in prefs.idiomas:
        return ResultadoPrefiltro(True, f"idioma no aceptado: {idioma}")

    normalizado = normaliza(texto)
    for palabra in [*prefs.tecnologias_veto, *prefs.sectores_veto]:
        if normaliza(palabra) in normalizado:
            return ResultadoPrefiltro(True, f"palabra vetada: {normaliza(palabra)}")

    if job.modalidad != "desconocida" and job.modalidad not in prefs.modalidades:
        return ResultadoPrefiltro(True, f"modalidad no aceptada: {job.modalidad}")

    if job.modalidad in ("presencial", "hibrido") and prefs.zonas:
        ubicacion = normaliza(job.ubicacion)
        if not any(normaliza(z) in ubicacion for z in prefs.zonas):
            return ResultadoPrefiltro(True, f"zona fuera de rango: {job.ubicacion}")

    if prefs.salario_min is not None and job.salario_max is not None:
        if job.salario_max < prefs.salario_min:
            return ResultadoPrefiltro(True, f"salario por debajo del mínimo: {job.salario_max}")

    return ResultadoPrefiltro(False)
