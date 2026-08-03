import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

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

# Caracteres que cuentan como parte de un token técnico. Incluyen '+' y '#' a
# propósito: son lo único que distingue 'c' de 'c++' y de 'c#'.
_CARACTERES_TOKEN = "a-z0-9+#_"


def _sin_acentos(texto: str) -> str:
    """Minúsculas y sin acentos, pero CONSERVANDO la puntuación.

    No se usa normaliza() de dedup.py porque ésa sustituye la puntuación por
    espacios, y entonces 'c', 'c++' y 'c#' colapsan al mismo token y 'node.js'
    se parte en dos palabras. Para deduplicar da igual; para vetar, no.
    """
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


@lru_cache(maxsize=512)
def _patron_veto(termino: str) -> re.Pattern[str] | None:
    """Compila un veto a una expresión que casa por token completo.

    Consecuencias deliberadas de tratar '+', '#' y '_' como parte del token:

    - un veto de 'c' NO descarta una oferta de C++ ni de C#, porque 'c++' y 'c#'
      son tokens distintos. Para vetarlos hay que escribirlos enteros. Es la
      lectura conservadora del principio del módulo: ante la duda, no descarta.
    - un veto de 'c' SÍ descarta "experiencia en C y assembly": ahí 'c' va sola.
    - el punto y el guion sí separan tokens, así que un veto de 'node' descarta
      "Node.js" y un veto de 'node.js' sólo casa si la oferta lo escribe con punto.
    - los vetos de varias palabras casan aunque las separen varios espacios o un
      salto de línea ('business intelligence' partido entre dos líneas).
    """
    partes = [re.escape(p) for p in _sin_acentos(termino).split()]
    if not partes:
        return None
    cuerpo = r"\s+".join(partes)
    return re.compile(
        rf"(?<![{_CARACTERES_TOKEN}]){cuerpo}(?![{_CARACTERES_TOKEN}])"
    )


def _esta_vetado(texto: str, termino: str) -> bool:
    patron = _patron_veto(termino)
    return patron is not None and patron.search(texto) is not None


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

    sin_acentos = _sin_acentos(texto)
    for palabra in [*prefs.tecnologias_veto, *prefs.sectores_veto]:
        if _esta_vetado(sin_acentos, palabra):
            return ResultadoPrefiltro(True, f"palabra vetada: {_sin_acentos(palabra)}")

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
