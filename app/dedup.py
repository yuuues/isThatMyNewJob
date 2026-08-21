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


def hash_dedup(empresa: str | None, titulo: str | None) -> str:
    """Clave canónica de una oferta, independiente de la fuente que la sirvió.

    La ubicación NO entra en la clave, aunque el spec la incluía. Es el campo menos
    estable de una oferta y medirlo sobre datos reales lo dejó claro por dos vías:

    - Adzuna republica un mismo anuncio como un listado por provincia, cada uno con su
      `id`: nueve filas de la misma oferta de PayXpert, cada una con su scrape y su
      llamada al modelo.
    - Cada fuente escribe la ubicación a su manera. Para la misma oferta, adzuna dice
      "Barcelona" y scrappa "08029 Barcelona, Barcelona provincia"; o adzuna dice
      "España" donde scrappa dice "Valencia, Valencia provincia". Con la ubicación
      dentro, la clave que existe justamente para reconocer la misma oferta llegada por
      fuentes distintas no casaba casi nunca.

    El coste asumido: dos vacantes distintas de la misma empresa con el mismo título en
    ciudades distintas colapsan en una. Se prefiere a lo contrario porque la ubicación
    no se pierde — se acumula en `Job.ubicaciones` — y el prefiltro las mira todas.
    """
    partes = [normaliza_empresa(empresa), normaliza(titulo)]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()


def ubicaciones_conocidas(oferta) -> list[str]:
    """Todas las ubicaciones de una oferta, con la principal siempre la primera.

    Sirve igual para un `Job` que para un `RawJob`. La lista puede venir vacía o a None:
    `asegura_esquema()` añade `ubicaciones` sin valor por defecto, así que las filas
    anteriores a la columna la tienen a NULL y sólo saben de su `ubicacion`.
    """
    principal = oferta.ubicacion
    resto = [u for u in (oferta.ubicaciones or []) if u and u != principal]
    return ([principal] if principal else []) + resto
