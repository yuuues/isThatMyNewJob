"""Descripción completa de una oferta, leída de la ficha pública de Adzuna.

La API corta las descripciones a 500 caracteres y no ofrece el texto completo por
ningún campo (ver app/sources/adzuna.py). La ficha web sí lo publica, y `redirect_url`
ya apunta a ella.

Medido el 2026-08-06 sobre 10 ofertas reales:

- Las 10 respondieron 200, sin challenge de JavaScript, cookies ni login.
- `<section class="adp-body">` apareció en las 10, con 1078-3673 caracteres (mediana
  ~2100) frente a los 500 de la API.
- El `JobPosting` del JSON-LD traía el mismo texto, pero faltaba en 1 de las 10. Por eso
  es la reserva y no la fuente principal.
- `robots.txt` no prohíbe `/details/` (Adzuna publica un sitemap de esa ruta). Lo
  prohibido es `/land/ad/` y `/goto/ad/`, el salto al portal del anunciante, que no
  usamos.

El HTML se parsea con `re` y no con BeautifulSoup para no añadir una dependencia por un
único selector, igual que hace app/sources/remotive.py.
"""

import json
import re
from html import unescape
from urllib.parse import urlsplit, urlunsplit

# No codicioso a propósito: la ficha trae más <section> después (ofertas similares), y
# sin el `?` la descripción se las tragaría hasta el último cierre de la página.
#
# El riesgo simétrico —una <section> anidada DENTRO de adp-body, que este corte
# truncaría en silencio— está descartado por medición: en 9 de las 10 fichas de la
# muestra el texto extraído aquí coincidía carácter a carácter con el `description` del
# JSON-LD, y con una anidada las longitudes no cuadrarían. Si algún día una descripción
# sale sospechosamente corta, éste es el primer sitio donde mirar.
#
# Se asume también que Adzuna maqueta con comillas dobles y etiquetas en minúscula, como
# hace hoy. Si dejara de hacerlo, la extracción no rompe: cae al JSON-LD.
_SECCION_CUERPO = re.compile(
    r'<section[^>]*class="[^"]*adp-body[^"]*"[^>]*>(.*?)</section>', re.S
)
_BLOQUES_LD_JSON = re.compile(
    r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S
)

# Las etiquetas que separan bloques se convierten en salto de línea antes de borrar el
# resto. `html_a_texto()` de remotive.py colapsa todo el espacio en blanco a espacios
# simples, lo que convertiría una descripción de 3000 caracteres con viñetas en un
# párrafo corrido ilegible. No se toca aquella función: funciona para lo suyo.
_ETIQUETAS_DE_BLOQUE = re.compile(r"<br\s*/?>|</(?:p|div|li|h[1-6]|tr)>", re.I)
_ETIQUETAS = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"[ \t]{2,}")
_LINEAS_VACIAS = re.compile(r"\n{3,}")


class DescripcionNoDisponible(Exception):
    """La ficha ya no existe (404/410). No tiene sentido reintentarlo."""


def url_ficha(url: str) -> str:
    """Deja `https://www.adzuna.es/details/<id>`, sin query ni fragmento.

    `redirect_url` llega con `?utm_medium=api&utm_source=...`. El robots.txt de Adzuna
    prohíbe varios patrones con query; el nuestro no está entre ellos, pero pedir la URL
    limpia evita el problema entero y además hace la petición idéntica entre runs.
    """
    partes = urlsplit(url)
    return urlunsplit((partes.scheme, partes.netloc, partes.path, "", ""))


def html_a_texto(html: str) -> str:
    """HTML a texto plano conservando la separación entre bloques."""
    texto = _ETIQUETAS_DE_BLOQUE.sub("\n", html)
    texto = unescape(_ETIQUETAS.sub(" ", texto))
    texto = _ESPACIOS.sub(" ", texto)
    texto = "\n".join(linea.strip() for linea in texto.split("\n"))
    return _LINEAS_VACIAS.sub("\n\n", texto).strip()


def _del_json_ld(html: str) -> str:
    """El `description` del JobPosting, buscándolo entre TODOS los bloques ld+json.

    Las fichas traen varios (WebSite, BreadcrumbList...). Coger el primero devuelve
    cadena vacía la mayoría de las veces.
    """
    for bloque in _BLOQUES_LD_JSON.findall(html):
        try:
            datos = json.loads(bloque)
        except ValueError:
            continue
        for entrada in datos if isinstance(datos, list) else [datos]:
            if isinstance(entrada, dict) and entrada.get("@type") == "JobPosting":
                texto = html_a_texto(entrada.get("description") or "")
                if texto:
                    return texto
    return ""


def extrae_descripcion(html: str) -> str:
    """Texto de la oferta: primero `adp-body`, y el JSON-LD como reserva.

    Que falten los dos se trata como RuntimeError y NO como DescripcionNoDisponible:
    significa que Adzuna cambió la maquetación, y eso se reintenta al día siguiente en
    lugar de darse por perdido para siempre.
    """
    seccion = _SECCION_CUERPO.search(html)
    if seccion:
        texto = html_a_texto(seccion.group(1))
        if texto:
            return texto

    texto = _del_json_ld(html)
    if texto:
        return texto

    raise RuntimeError("La ficha de Adzuna no trae ni adp-body ni JobPosting")
