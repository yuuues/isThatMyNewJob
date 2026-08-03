import hashlib
import re
import unicodedata

_FORMAS_JURIDICAS = {
    "sl", "slu", "sa", "sau", "sociedad", "limitada",
    "inc", "llc", "ltd", "ltda", "corp", "gmbh", "ag", "bv", "nv", "srl", "spa", "oy", "ab",
}


def normaliza(texto: str | None) -> str:
    """Minúsculas, sin acentos, sin puntuación y con espacios colapsados."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normaliza_empresa(empresa: str | None) -> str:
    """Como normaliza(), quitando además la forma jurídica.

    'Acme S.L.' y 'ACME SL' son la misma empresa y deben deduplicar juntas.

    Los puntos se eliminan antes de normalizar, no se sustituyen por espacio:
    'S.L.' debe colapsar a 'sl' para que el filtro de formas jurídicas la
    reconozca. Si se dejara a normaliza(), quedaría 's l' y no coincidiría.
    """
    sin_puntos = (empresa or "").replace(".", "")
    palabras = [p for p in normaliza(sin_puntos).split() if p not in _FORMAS_JURIDICAS]
    return " ".join(palabras)


def hash_dedup(empresa: str | None, titulo: str | None, ubicacion: str | None) -> str:
    """Clave canónica de una oferta, independiente de la fuente que la sirvió."""
    partes = [normaliza_empresa(empresa), normaliza(titulo), normaliza(ubicacion)]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()
