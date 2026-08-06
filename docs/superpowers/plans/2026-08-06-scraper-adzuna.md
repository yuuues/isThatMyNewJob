# Descripción completa de las ofertas de Adzuna — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leer la ficha pública de cada oferta de Adzuna por HTTP para sustituir la descripción truncada a 500 caracteres por el texto completo, recalcular la modalidad y devolver la oferta a la cola de clasificación.

**Architecture:** Dos módulos con frontera limpia. `app/sources/adzuna_web.py` sólo sabe de HTTP y HTML (URL entra, texto sale; sin ORM). `app/enrich.py` sólo sabe de base de datos (recorre `Job`, llama al scraper, escribe y resetea). El paso se inyecta en `ejecuta_run()` entre `ingesta()` y el bucle de clasificación, de modo que el prefiltro y el clasificador ven ya el texto completo.

**Tech Stack:** Python 3.13, httpx, SQLAlchemy 2.0, pydantic-settings, pytest + respx. **Sin dependencias nuevas**: el HTML se parsea con `re`, como ya hace `app/sources/remotive.py`.

**Spec:** `docs/superpowers/specs/2026-08-06-scraper-adzuna-design.md`

---

## Contexto que el implementador necesita antes de empezar

Cinco cosas de este proyecto que no se deducen del código y que, si se ignoran, producen un cambio que parece funcionar y no funciona:

1. **Las migraciones las hace `asegura_esquema()` en `app/db.py`, no Alembic.** Añade columnas nuevas a tablas existentes **sin `NOT NULL` y sin valor por defecto**, porque SQLite no permite otra cosa sobre una tabla con filas. Consecuencia directa: las 138 filas actuales de `job` tendrán `intentos_scrape` a **NULL, no a 0**, y `WHERE intentos_scrape < 3` **no las selecciona** (`NULL < 3` es `NULL`, que no es verdadero). Esto tiene un test dedicado en la Task 4.

2. **La modalidad desconocida está exenta de dos reglas del prefiltro** (`app/prefilter.py:121` y `:124`). Recalcular la modalidad hará que el prefiltro **descarte más** ofertas, no menos, y son descartes correctos. No es una regresión.

3. **`respx` es la forma de simular HTTP en esta suite.** Se usa como decorador `@respx.mock`. Ver `tests/test_sources_adzuna.py`.

4. **El fixture `sesion`** de `tests/conftest.py` da una sesión SQLAlchemy sobre SQLite en memoria con el esquema ya creado.

5. **Comentarios: se explica el porqué, no el qué.** Este proyecto documenta las decisiones y lo medido, no la sintaxis. Mira `app/sources/adzuna.py:103-119` como referencia de tono. Los comentarios de este plan están escritos para copiarse tal cual.

**Comando de tests:** `python -m pytest` desde la raíz del repo. Los tests marcados `contrato` están excluidos por defecto (`pyproject.toml`).

---

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `app/sources/adzuna_web.py` (crear) | HTTP + HTML → texto. Sin ORM, sin sesión, sin `Job`. |
| `app/enrich.py` (crear) | Recorrer `Job`, llamar al scraper, escribir y resetear. Sin HTTP. |
| `app/models.py` (modificar) | Columna `intentos_scrape`. |
| `app/config.py` (modificar) | Los cuatro ajustes del paso. |
| `app/pipeline.py` (modificar) | Llamar al paso entre `ingesta()` y el bucle. |
| `app/cli.py` (modificar) | Construir el enriquecedor y pasarlo. |
| `app/web/routes_config.py` (modificar) | Lo mismo, para el botón "buscar ahora". |
| `tests/fixtures/adzuna_ficha.html` (crear) | Ficha recortada, ~4 KB. |
| `tests/fixtures/adzuna_ficha_sin_seccion.html` (crear) | Sólo JSON-LD, para la reserva. |
| `tests/test_sources_adzuna_web.py` (crear) | Tests del scraper. |
| `tests/test_enrich.py` (crear) | Tests del paso y del reset. |
| `tests/test_pipeline.py` (modificar) | Test de integración. |
| `tests/test_config.py` (modificar) | Ajustes nuevos. |

---

## Task 1: La columna `intentos_scrape`

**Files:**
- Modify: `app/models.py:82`
- Test: `tests/test_models.py`

- [ ] **Step 1: Escribe el test que falla**

Añade al final de `tests/test_models.py`:

```python
def test_una_oferta_nueva_arranca_sin_intentos_de_scrape(sesion):
    """El contador nace a 0 para que el paso de enriquecimiento pueda contar fallos."""
    from app.models import Job

    job = Job(
        fuente="adzuna",
        external_id="1",
        url="https://www.adzuna.es/details/1",
        titulo="Backend",
        empresa="Empresa",
        descripcion="Texto",
        hash_dedup="abc",
    )
    sesion.add(job)
    sesion.commit()

    assert job.intentos_scrape == 0
```

- [ ] **Step 2: Ejecuta el test y comprueba que falla**

Run: `python -m pytest tests/test_models.py::test_una_oferta_nueva_arranca_sin_intentos_de_scrape -v`
Expected: FAIL con `TypeError: 'intentos_scrape' is an invalid keyword argument` o `AttributeError`.

- [ ] **Step 3: Añade la columna**

En `app/models.py`, justo después de la línea `intentos_clasificacion: Mapped[int] = mapped_column(Integer, default=0)`:

```python
    # Fallos del scraper de la ficha pública, para no reintentar eternamente una oferta
    # que Adzuna ya borró. Ver app/enrich.py. Ojo: `asegura_esquema()` añade esta columna
    # a las bases existentes SIN valor por defecto, así que las filas antiguas la tienen
    # a NULL y no a 0. Quien la consulte en SQL debe contemplar el NULL.
    intentos_scrape: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: Ejecuta el test y comprueba que pasa**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS, y el resto de tests del fichero siguen pasando.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "Añade el contador de intentos del scraper de Adzuna"
```

---

## Task 2: Los fixtures HTML

**Files:**
- Create: `tests/fixtures/adzuna_ficha.html`
- Create: `tests/fixtures/adzuna_ficha_sin_seccion.html`

No hay test en esta tarea: son datos para las siguientes. Se recortan a mano en lugar de guardar los 80 KB reales.

- [ ] **Step 1: Crea la ficha completa**

Crea `tests/fixtures/adzuna_ficha.html` con exactamente esto:

```html
<!DOCTYPE html>
<html lang="es">
<head>
<title>Desarrollador Backend - Empresa Ejemplo | Adzuna</title>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"JobPosting","title":"Desarrollador Backend",
"description":"<p>Buscamos desarrollador backend.</p><p>El puesto es para nuestra oficina de Sevilla en formato H&iacute;brido.</p>",
"hiringOrganization":{"@type":"Organization","name":"Empresa Ejemplo"}}
</script>
</head>
<body>
<main>
<h1>Desarrollador Backend</h1>
<section class="adp-body adp-body--desktop">
<p>Buscamos desarrollador backend con experiencia en Python.</p>
<p>Requisitos:</p>
<ul>
<li>Cinco a&ntilde;os de experiencia</li>
<li>Conocimientos de SQL &amp; Docker</li>
</ul>
<p>El puesto es para nuestra oficina de Sevilla en formato H&iacute;brido.</p>
</section>
<section class="adp-related">
<p>Ofertas similares</p>
</section>
</main>
</body>
</html>
```

Dos cosas del fixture que no son decorativas: el texto de la modalidad va **sólo en el último párrafo**, que es justo lo que la API trunca, y hay una **segunda `<section>` después** para comprobar que la extracción no se pasa de largo y se traga el bloque de "ofertas similares".

- [ ] **Step 2: Crea la ficha sin `adp-body`**

Crea `tests/fixtures/adzuna_ficha_sin_seccion.html`:

```html
<!DOCTYPE html>
<html lang="es">
<head>
<title>Desarrollador Backend - Empresa Ejemplo | Adzuna</title>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"WebSite","name":"Adzuna"}
</script>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"JobPosting","title":"Desarrollador Backend",
"description":"<p>Descripci&oacute;n s&oacute;lo disponible en el JSON-LD.</p><p>Modalidad h&iacute;brida.</p>"}
</script>
</head>
<body>
<main><h1>Desarrollador Backend</h1></main>
</body>
</html>
```

Reproduce lo medido: 1 de cada 10 fichas no trae `adp-body`, y las páginas llevan **más de un** bloque JSON-LD, así que hay que buscar el `JobPosting` entre ellos y no coger el primero.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/adzuna_ficha.html tests/fixtures/adzuna_ficha_sin_seccion.html
git commit -m "Añade fichas de Adzuna recortadas como fixture"
```

---

## Task 3: Extracción del texto (sin red)

**Files:**
- Create: `app/sources/adzuna_web.py`
- Test: `tests/test_sources_adzuna_web.py`

- [ ] **Step 1: Escribe los tests que fallan**

Crea `tests/test_sources_adzuna_web.py`:

```python
from pathlib import Path

import pytest

from app.sources.adzuna_web import DescripcionNoDisponible, extrae_descripcion

FIXTURES = Path(__file__).parent / "fixtures"
FICHA = (FIXTURES / "adzuna_ficha.html").read_text(encoding="utf-8")
FICHA_SIN_SECCION = (FIXTURES / "adzuna_ficha_sin_seccion.html").read_text(encoding="utf-8")


def test_saca_el_texto_de_la_seccion_adp_body():
    texto = extrae_descripcion(FICHA)

    assert "experiencia en Python" in texto
    assert "oficina de Sevilla en formato Híbrido" in texto


def test_no_se_traga_las_secciones_siguientes():
    """La expresión debe parar en el primer </section>, no en el último.

    Sin el modo no codicioso, la descripción se llevaría el bloque de "Ofertas
    similares" y el clasificador razonaría sobre puestos que no son el de la oferta.
    """
    assert "Ofertas similares" not in extrae_descripcion(FICHA)


def test_conserva_la_estructura_en_saltos_de_linea():
    """Una descripción de 3000 caracteres sin saltos es ilegible en la ficha web."""
    texto = extrae_descripcion(FICHA)

    assert "Requisitos:" in texto
    assert "\n" in texto
    assert "  " not in texto  # nada de dobles espacios sueltos


def test_traduce_las_entidades_html():
    texto = extrae_descripcion(FICHA)

    assert "años" in texto
    assert "SQL & Docker" in texto
    assert "&amp;" not in texto


def test_cae_al_json_ld_cuando_falta_la_seccion():
    """Medido: `adp-body` estaba en 10 de 10 fichas y el JSON-LD faltaba en 1.

    De ahí el orden. Pero cuando la sección falta, el JobPosting salva la oferta, y
    hay que encontrarlo entre varios bloques ld+json, no coger el primero.
    """
    texto = extrae_descripcion(FICHA_SIN_SECCION)

    assert "sólo disponible en el JSON-LD" in texto
    assert "Modalidad híbrida." in texto


def test_sin_seccion_ni_json_ld_falla_de_forma_reintentable():
    """Un HTML irreconocible NO es DescripcionNoDisponible.

    Esa excepción significa "la oferta ya no existe" y agota los intentos de golpe.
    Un cambio de maquetación de Adzuna debe reintentarse, no darse por perdido.
    """
    with pytest.raises(RuntimeError) as fallo:
        extrae_descripcion("<html><body><p>Nada útil</p></body></html>")

    assert not isinstance(fallo.value, DescripcionNoDisponible)
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_sources_adzuna_web.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.sources.adzuna_web'`.

- [ ] **Step 3: Escribe el módulo**

Crea `app/sources/adzuna_web.py`:

```python
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

import httpx

from app.limitador import LimitadorPorHost

# No codicioso a propósito: la ficha trae más <section> después (ofertas similares), y
# sin el `?` la descripción se las tragaría hasta el último cierre de la página.
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
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_sources_adzuna_web.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add app/sources/adzuna_web.py tests/test_sources_adzuna_web.py
git commit -m "Extrae la descripción completa del HTML de la ficha de Adzuna"
```

---

## Task 4: La descarga (HTTP)

**Files:**
- Modify: `app/sources/adzuna_web.py`
- Test: `tests/test_sources_adzuna_web.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade a `tests/test_sources_adzuna_web.py`. Cambia también la línea de importación de arriba por esta:

```python
from app.sources.adzuna_web import (
    CABECERAS,
    DescripcionNoDisponible,
    descarga_descripcion,
    extrae_descripcion,
    url_ficha,
)
```

y añade al principio del fichero, junto a los otros imports:

```python
import httpx
import respx

from app.limitador import sin_espera
```

Tests nuevos, al final:

```python
FICHA_URL = "https://www.adzuna.es/details/5812188567"


class LimitadorEspia:
    def __init__(self) -> None:
        self.turnos: list[str] = []

    def espera_turno(self, url: str) -> float:
        self.turnos.append(url)
        return 0.0


def test_quita_el_query_string_de_la_url():
    sucia = "https://www.adzuna.es/details/123?utm_medium=api&utm_source=a1e935a6"

    assert url_ficha(sucia) == "https://www.adzuna.es/details/123"


@respx.mock
def test_descarga_y_devuelve_el_texto_de_la_ficha():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))

    texto = descarga_descripcion(f"{FICHA_URL}?utm_medium=api", limitador=sin_espera())

    assert "oficina de Sevilla en formato Híbrido" in texto


@respx.mock
def test_manda_accept_y_accept_language():
    """Medido: CloudFront devuelve 403 sin estas dos cabeceras, sea cual sea el UA.

    Un user-agent de Chrome sin ellas se lleva el mismo 403 que uno propio, así que no
    hace falta fingir ser un navegador y nos identificamos con nuestro nombre.
    """
    ruta = respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))

    descarga_descripcion(FICHA_URL, limitador=sin_espera())

    enviadas = ruta.calls.last.request.headers
    assert enviadas["accept-language"].startswith("es-ES")
    assert "text/html" in enviadas["accept"]
    assert enviadas["user-agent"] == CABECERAS["User-Agent"]
    assert "isThatMyNewJob" in enviadas["user-agent"]


@respx.mock
def test_un_404_significa_que_la_oferta_ya_no_existe():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(404, text="no such job"))

    with pytest.raises(DescripcionNoDisponible):
        descarga_descripcion(FICHA_URL, limitador=sin_espera())


@respx.mock
def test_un_410_tambien():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(410, text="gone"))

    with pytest.raises(DescripcionNoDisponible):
        descarga_descripcion(FICHA_URL, limitador=sin_espera())


@respx.mock
def test_un_403_es_reintentable_y_no_da_la_oferta_por_perdida():
    """Un bloqueo del WAF es transitorio y afecta a todas las ofertas por igual.

    Tratarlo como DescripcionNoDisponible marcaría el atraso entero como
    definitivamente fallido por culpa de un bloqueo de una tarde.
    """
    respx.get(FICHA_URL).mock(return_value=httpx.Response(403, text="Request blocked"))

    with pytest.raises(RuntimeError) as fallo:
        descarga_descripcion(FICHA_URL, limitador=sin_espera())

    assert not isinstance(fallo.value, DescripcionNoDisponible)
    assert "403" in str(fallo.value)


@respx.mock
def test_pide_turno_al_limitador_con_la_url_ya_limpia():
    respx.get(FICHA_URL).mock(return_value=httpx.Response(200, text=FICHA))
    espia = LimitadorEspia()

    descarga_descripcion(f"{FICHA_URL}?utm_medium=api", limitador=espia)

    assert espia.turnos == [FICHA_URL]
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_sources_adzuna_web.py -v`
Expected: FAIL con `ImportError: cannot import name 'descarga_descripcion'`.

- [ ] **Step 3: Añade la descarga**

Añade a `app/sources/adzuna_web.py`, después de los `re.compile` y antes de la clase `DescripcionNoDisponible`:

```python
# Medido el 2026-08-06 contra /details/5812188567, caso por caso: httpx por defecto,
# 403. UA propio a secas, 403. UA de Chrome sin `Accept`, 403. UA propio CON `Accept` y
# `Accept-Language`, 200. Al WAF de CloudFront no le importa quién dice ser el cliente,
# le importa que mande estas dos cabeceras. Por eso nos identificamos con nuestro nombre
# en vez de disfrazarnos de navegador: no hace falta y sería mentir sin ganar nada.
CABECERAS = {
    "User-Agent": "isThatMyNewJob/1.0 (uso personal; +kevin@kcsystem.es)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# El único Crawl-delay que Adzuna publica en su robots.txt (para bingbot) son 2
# segundos. A falta de una cifra para nosotros, se usa la suya.
INTERVALO_SEGUNDOS = 2.0

# Adzuna deja de servir la ficha de una oferta retirada. No es un fallo transitorio: la
# oferta no va a volver, y reintentarla cada día sería gastar cupo en confirmar que algo
# borrado sigue borrado.
_CODIGOS_DEFINITIVOS = (404, 410)
```

Y al final del fichero:

```python
def descarga_descripcion(
    url: str,
    *,
    limitador: LimitadorPorHost | None = None,
    timeout: float = 30.0,
) -> str:
    """Texto completo de la oferta cuya ficha vive en `url`.

    Lanza `DescripcionNoDisponible` si la oferta ya no existe, y `RuntimeError` para
    cualquier otro fallo, que sí es reintentable. La distinción la usa app/enrich.py
    para decidir entre agotar los intentos de golpe o sumar uno.
    """
    ficha = url_ficha(url)
    limitador = limitador or LimitadorPorHost(intervalo_por_defecto=INTERVALO_SEGUNDOS)
    limitador.espera_turno(ficha)

    respuesta = httpx.get(ficha, headers=CABECERAS, timeout=timeout, follow_redirects=True)

    if respuesta.status_code in _CODIGOS_DEFINITIVOS:
        raise DescripcionNoDisponible(
            f"Adzuna ya no publica {ficha} (respondió {respuesta.status_code})"
        )

    try:
        respuesta.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"La ficha de Adzuna respondió {respuesta.status_code}: {respuesta.text[:200]}"
        ) from e

    return extrae_descripcion(respuesta.text)
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_sources_adzuna_web.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/sources/adzuna_web.py tests/test_sources_adzuna_web.py
git commit -m "Descarga la ficha de Adzuna identificándose con su propio user-agent"
```

---

## Task 5: La selección de ofertas a enriquecer

**Files:**
- Create: `app/enrich.py`
- Test: `tests/test_enrich.py`

Esta tarea es sólo la consulta. El bucle viene en la Task 6.

- [ ] **Step 1: Escribe los tests que fallan**

Crea `tests/test_enrich.py`:

```python
from datetime import datetime

from app.enrich import MAX_INTENTOS_SCRAPE, pendientes_de_enriquecer
from app.models import Job


def crea_job(sesion, external_id="1", **kwargs) -> Job:
    base = dict(
        fuente="adzuna",
        external_id=external_id,
        url=f"https://www.adzuna.es/details/{external_id}",
        titulo="Backend Developer",
        empresa="Empresa",
        ubicacion="Sevilla",
        descripcion="Extracto corto de la oferta…",
        descripcion_truncada=True,
        hash_dedup=f"hash-{external_id}",
        ingerida_en=datetime(2026, 8, 1, 10, 0),
    )
    base.update(kwargs)
    job = Job(**base)
    sesion.add(job)
    sesion.commit()
    return job


def test_coge_las_ofertas_truncadas_de_adzuna(sesion):
    crea_job(sesion, "1")

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 10)] == ["1"]


def test_ignora_las_ofertas_ya_completas(sesion):
    crea_job(sesion, "1", descripcion_truncada=False)

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_ignora_las_de_otras_fuentes(sesion):
    crea_job(sesion, "1", fuente="scrappa")

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_ignora_las_que_ya_agotaron_los_intentos(sesion):
    crea_job(sesion, "1", intentos_scrape=MAX_INTENTOS_SCRAPE)

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_coge_las_filas_heredadas_con_intentos_a_null(sesion):
    """La regresión que la migración deja servida en bandeja.

    `asegura_esquema()` añade `intentos_scrape` SIN valor por defecto, así que las 136
    ofertas del atraso la tienen a NULL. En SQL, `NULL < 3` es NULL, que no es
    verdadero: un `WHERE intentos_scrape < 3` las dejaría fuera y el atraso entero sería
    invisible, sin ningún error a la vista.
    """
    job = crea_job(sesion, "1")
    job.intentos_scrape = None
    sesion.commit()

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 10)] == ["1"]


def test_respeta_el_tope(sesion):
    for i in range(5):
        crea_job(sesion, str(i))

    assert len(pendientes_de_enriquecer(sesion, 2)) == 2


def test_empieza_por_lo_mas_recien_ingerido(sesion):
    """Con 136 de atraso y un tope de 40, el orden ascendente haría que las ofertas de
    hoy —las que se van a clasificar en este mismo run— esperasen cuatro días."""
    crea_job(sesion, "vieja", ingerida_en=datetime(2026, 8, 1, 10, 0))
    crea_job(sesion, "nueva", ingerida_en=datetime(2026, 8, 6, 10, 0))

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 1)] == ["nueva"]
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.enrich'`.

- [ ] **Step 3: Escribe la consulta**

Crea `app/enrich.py`:

```python
"""Completa las descripciones que Adzuna sirve truncadas, leyendo su ficha pública.

Este módulo sabe de base de datos y no sabe de HTTP: el scraper entra como una función
inyectada. La frontera importa porque el scraper se prueba con HTML fijo y este módulo
con una base de datos en memoria, sin que ninguno de los dos necesite al otro.

Corre entre `ingesta()` y el bucle de clasificación, nunca después: el prefiltro decide
por modalidad, y la modalidad sólo es fiable con el texto completo.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Job

FUENTE = "adzuna"

# Mismo tope que `MAX_INTENTOS` de app/pipeline.py, y por el mismo motivo: una oferta
# que falla tres veces deja de gastar peticiones pero sigue consultable.
MAX_INTENTOS_SCRAPE = 3


def pendientes_de_enriquecer(sesion: Session, limite: int) -> list[Job]:
    """Ofertas de Adzuna truncadas que aún tienen intentos, la más reciente primero.

    Dos detalles que no son estilo:

    `intentos_scrape` puede ser NULL en las filas anteriores a la columna, porque
    `asegura_esquema()` la añade sin valor por defecto (ver app/db.py). Un
    `intentos_scrape < MAX` a secas descarta esas filas en silencio, y son justo las 136
    del atraso.

    El orden es descendente para que el atraso no desplace a las ofertas del día, que
    son las que se van a clasificar en este mismo run.
    """
    return list(
        sesion.scalars(
            select(Job)
            .where(
                Job.fuente == FUENTE,
                Job.descripcion_truncada.is_(True),
                or_(
                    Job.intentos_scrape.is_(None),
                    Job.intentos_scrape < MAX_INTENTOS_SCRAPE,
                ),
            )
            .order_by(Job.ingerida_en.desc())
            .limit(limite)
        )
    )
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/enrich.py tests/test_enrich.py
git commit -m "Selecciona las ofertas de Adzuna pendientes de enriquecer"
```

---

## Task 6: El paso: éxito, modalidad y reset

**Files:**
- Modify: `app/enrich.py`
- Test: `tests/test_enrich.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade a `tests/test_enrich.py`. Amplía la línea de import de arriba:

```python
from app.enrich import MAX_INTENTOS_SCRAPE, enriquece_descripciones, pendientes_de_enriquecer
from app.models import Clasificacion, Decision, Job
```

Y añade estos tests al final:

```python
TEXTO_LARGO = (
    "Buscamos desarrollador backend con experiencia en Python.\n\n"
    "El puesto es para nuestra oficina de Sevilla en formato Híbrido."
)


def scraper_que_devuelve(texto=TEXTO_LARGO):
    def scraper(url: str) -> str:
        return texto

    return scraper


def crea_clasificacion(sesion, job, categoria="revisar") -> Clasificacion:
    fila = Clasificacion(
        job_id=job.id,
        categoria=categoria,
        confianza="media",
        razonamiento="Juzgada con el extracto de 500 caracteres.",
        ejes={"tecnico": "ok", "seniority": "ok", "modalidad": "?", "salario": "?", "sector": "ok"},
        modelo="deepseek-v4-flash",
        prompt_version=1,
    )
    sesion.add(fila)
    sesion.commit()
    return fila


def test_guarda_el_texto_completo_y_apaga_la_marca(sesion):
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.descripcion == TEXTO_LARGO
    assert job.descripcion_truncada is False
    assert resumen.completadas == 1


def test_recalcula_la_modalidad_con_el_texto_completo(sesion):
    """El corazón del cambio.

    La modalidad se dedujo del extracto de 500 caracteres, donde no se menciona, así que
    la oferta quedó como "desconocida". Y la modalidad desconocida está exenta de la
    regla de zona del prefiltro (app/prefilter.py:124), así que una oferta híbrida en
    Sevilla se colaba entera hasta el clasificador.
    """
    job = crea_job(sesion, "1", modalidad="desconocida")

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.modalidad == "hibrido"


def test_devuelve_la_oferta_a_la_cola_y_borra_el_veredicto_viejo(sesion):
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.estado_clasificacion == "pendiente"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is None
    assert resumen.reevaluadas == 1


def test_una_descartada_por_regla_vuelve_a_la_cola_sin_motivo(sesion):
    """Son las que más lo necesitan: su descarte se decidió con una modalidad inventada."""
    job = crea_job(
        sesion,
        "1",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="zona fuera de rango: Madrid",
    )

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.estado_clasificacion == "pendiente"
    assert job.motivo_regla is None


def test_no_resetea_una_oferta_que_el_usuario_ya_decidio(sesion):
    """Reopinar sobre algo que ya cerró a mano no aporta nada y la reabre en la lista."""
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="descartada_por_mi", motivo="No me interesa"))
    sesion.commit()

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.descripcion == TEXTO_LARGO  # el texto sí se completa
    assert job.estado_clasificacion == "clasificada"  # pero el veredicto se respeta
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None
    assert resumen.reevaluadas == 0


def test_una_oferta_ya_enriquecida_no_vuelve_a_entrar(sesion):
    """El test que cierra la duda del bucle infinito.

    El reset va atado al éxito del scrape, y lo primero que hace el éxito es apagar
    `descripcion_truncada`, que es la condición de la selección. Segunda pasada: nada.
    """
    crea_job(sesion, "1")

    primera = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)
    segunda = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    assert primera.completadas == 1
    assert segunda.intentadas == 0
```

Añade también `from sqlalchemy import select` al principio del fichero de tests.

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: FAIL con `ImportError: cannot import name 'enriquece_descripciones'`.

- [ ] **Step 3: Implementa el paso**

En `app/enrich.py`, amplía los imports de arriba:

```python
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import Clasificacion, Decision, Job
from app.sources.comun import detecta_modalidad
```

Y añade después de `MAX_INTENTOS_SCRAPE`:

```python
@dataclass
class ResumenEnriquecimiento:
    """Lo que hizo el paso. `fallos` no va a `run.stats`: va a `run.errores`."""

    intentadas: int = 0
    completadas: int = 0
    fallidas: int = 0
    agotadas: int = 0
    reevaluadas: int = 0
    cortado_por: str | None = None
    fallos: list[tuple[int, str]] = field(default_factory=list)

    def a_stats(self) -> dict:
        return {
            "intentadas": self.intentadas,
            "completadas": self.completadas,
            "fallidas": self.fallidas,
            "agotadas": self.agotadas,
            "reevaluadas": self.reevaluadas,
            "cortado_por": self.cortado_por,
        }
```

Y al final del fichero:

```python
def _reevalua(sesion: Session, job: Job) -> bool:
    """Devuelve la oferta a la cola para que se juzgue con el texto completo.

    El veredicto viejo se borra en vez de archivarse: `Clasificacion` tiene `job_id`
    único, y guardar historial pediría un cambio de modelo de datos para conservar una
    opinión emitida sobre datos malos.

    Las ofertas que el usuario ya decidió a mano se respetan. Comprobado antes de
    escribir esto: borrar clasificaciones no rompe el few-shot, porque
    `ejemplos_few_shot()` lee `Decision` y `Job` y nunca `Clasificacion`.
    """
    if sesion.scalar(select(Decision.id).where(Decision.job_id == job.id)) is not None:
        return False

    sesion.execute(delete(Clasificacion).where(Clasificacion.job_id == job.id))
    job.estado_clasificacion = "pendiente"
    job.motivo_regla = None
    return True


def enriquece_descripciones(
    sesion: Session,
    *,
    scraper: Callable[[str], str],
    max_por_run: int = 40,
) -> ResumenEnriquecimiento:
    """Completa las descripciones truncadas de Adzuna y devuelve esas ofertas a la cola.

    El commit es por oferta, como en el bucle de clasificación: un fallo a mitad no se
    lleva por delante el trabajo ya hecho.
    """
    resumen = ResumenEnriquecimiento()

    for job in pendientes_de_enriquecer(sesion, max_por_run):
        resumen.intentadas += 1
        texto = scraper(job.url)

        job.descripcion = texto
        job.descripcion_truncada = False
        job.modalidad = detecta_modalidad(f"{job.titulo} {texto}")
        resumen.completadas += 1

        if _reevalua(sesion, job):
            resumen.reevaluadas += 1

        sesion.commit()

    return resumen
```

Nota: el manejo de fallos llega en la Task 7. Aquí el scraper todavía no puede fallar.

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add app/enrich.py tests/test_enrich.py
git commit -m "Completa la descripción, recalcula la modalidad y reabre la clasificación"
```

---

## Task 7: Fallos, agotamiento y corte por racha

**Files:**
- Modify: `app/enrich.py`
- Test: `tests/test_enrich.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade a `tests/test_enrich.py`. Amplía el import del scraper:

```python
from app.sources.adzuna_web import DescripcionNoDisponible
```

Y estos tests al final:

```python
def scraper_que_falla(error):
    def scraper(url: str) -> str:
        raise error

    return scraper


def test_un_fallo_suma_un_intento_y_deja_la_oferta_truncada(sesion):
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("timeout")), max_por_run=10
    )

    sesion.refresh(job)
    assert job.intentos_scrape == 1
    assert job.descripcion_truncada is True
    assert resumen.fallidas == 1
    assert resumen.fallos == [(job.id, "RuntimeError: timeout")]


def test_un_fallo_no_toca_la_clasificacion_existente(sesion):
    """Si no hemos podido mejorar el dato, no hay motivo para tirar el veredicto."""
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)

    enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("timeout")), max_por_run=10
    )

    sesion.refresh(job)
    assert job.estado_clasificacion == "clasificada"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None


def test_una_oferta_borrada_agota_los_intentos_de_una_vez(sesion):
    """Reintentar tres runs para confirmar que algo borrado sigue borrado es tirar cupo."""
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(
        sesion,
        scraper=scraper_que_falla(DescripcionNoDisponible("ya no existe")),
        max_por_run=10,
    )

    sesion.refresh(job)
    assert job.intentos_scrape == MAX_INTENTOS_SCRAPE
    assert resumen.agotadas == 1
    assert pendientes_de_enriquecer(sesion, 10) == []


def test_una_racha_de_fallos_corta_el_paso(sesion):
    """Circuit breaker, en el espíritu del CuotaAgotadaError de pipeline.py.

    El día que Adzuna cambie el WAF y devuelva 403 a todo, sin este corte un run quemaría
    el cupo entero y tres runs bastarían para dar por perdido todo el atraso.
    """
    for i in range(10):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("403")), max_por_run=10
    )

    assert resumen.intentadas == MAX_FALLOS_SEGUIDOS
    assert resumen.cortado_por == MOTIVO_RACHA


def test_las_ofertas_borradas_no_alimentan_la_racha(sesion):
    """Un 404 demuestra que el servidor contesta, que es lo contrario de lo que la racha
    vigila. Drenar el atraso con seis ofertas retiradas seguidas es perfectamente
    posible y no debe cortar nada."""
    for i in range(6):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(
        sesion,
        scraper=scraper_que_falla(DescripcionNoDisponible("ya no existe")),
        max_por_run=10,
    )

    assert resumen.intentadas == 6
    assert resumen.cortado_por is None


def test_un_exito_reinicia_la_racha(sesion):
    intentos = {"n": 0}

    def scraper(url: str) -> str:
        intentos["n"] += 1
        if intentos["n"] % 2 == 0:
            return TEXTO_LARGO
        raise RuntimeError("timeout")

    for i in range(8):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(sesion, scraper=scraper, max_por_run=8)

    assert resumen.cortado_por is None
    assert resumen.intentadas == 8
```

Amplía también el import de `app.enrich`:

```python
from app.enrich import (
    MAX_FALLOS_SEGUIDOS,
    MAX_INTENTOS_SCRAPE,
    MOTIVO_RACHA,
    enriquece_descripciones,
    pendientes_de_enriquecer,
)
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: FAIL con `ImportError: cannot import name 'MAX_FALLOS_SEGUIDOS'`.

- [ ] **Step 3: Añade el manejo de fallos**

En `app/enrich.py`, añade el import del scraper y las dos constantes junto a `MAX_INTENTOS_SCRAPE`:

```python
from app.sources.adzuna_web import DescripcionNoDisponible
```

```python
# Circuit breaker. El día que Adzuna cambie el WAF y devuelva 403 a todo, sin este corte
# un run quemaría el cupo entero y en tres runs el atraso completo quedaría marcado como
# definitivamente fallido por culpa de un bloqueo temporal.
MAX_FALLOS_SEGUIDOS = 5
MOTIVO_RACHA = "racha_de_fallos"
```

Y sustituye el cuerpo del bucle de `enriquece_descripciones()` por este:

```python
    resumen = ResumenEnriquecimiento()
    seguidos = 0

    for job in pendientes_de_enriquecer(sesion, max_por_run):
        resumen.intentadas += 1

        try:
            texto = scraper(job.url)
        except DescripcionNoDisponible as e:
            # La oferta se retiró. Es un fallo de ESTA oferta, no del sitio: el servidor
            # contestó. Por eso agota sus intentos pero reinicia la racha, o drenar el
            # atraso con cinco ofertas retiradas seguidas cortaría el paso sin motivo.
            job.intentos_scrape = MAX_INTENTOS_SCRAPE
            resumen.agotadas += 1
            resumen.fallos.append((job.id, f"{type(e).__name__}: {e}"))
            seguidos = 0
            sesion.commit()
            continue
        except Exception as e:  # noqa: BLE001 - la oferta se reintenta en el run siguiente
            job.intentos_scrape = (job.intentos_scrape or 0) + 1
            resumen.fallidas += 1
            resumen.fallos.append((job.id, f"{type(e).__name__}: {e}"))
            seguidos += 1
            sesion.commit()
            if seguidos >= MAX_FALLOS_SEGUIDOS:
                resumen.cortado_por = MOTIVO_RACHA
                break
            continue

        seguidos = 0
        job.descripcion = texto
        job.descripcion_truncada = False
        job.modalidad = detecta_modalidad(f"{job.titulo} {texto}")
        resumen.completadas += 1

        if _reevalua(sesion, job):
            resumen.reevaluadas += 1

        sesion.commit()

    return resumen
```

El `(job.intentos_scrape or 0)` no es decorativo: en las filas heredadas la columna vale NULL, y `None + 1` es un `TypeError` que tumbaría el paso entero en la primera oferta del atraso que falle.

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_enrich.py -v`
Expected: PASS, 19 tests.

- [ ] **Step 5: Commit**

```bash
git add app/enrich.py tests/test_enrich.py
git commit -m "Acota los reintentos del scraper y corta ante una racha de fallos"
```

---

## Task 8: Los ajustes de configuración

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Escribe el test que falla**

Añade a `tests/test_config.py`:

```python
def test_los_ajustes_del_scraper_de_adzuna_tienen_valores_por_defecto(monkeypatch):
    """El tope existe para que el paso no eternice el run, no para limitar reintentos:
    de eso se encarga `intentos_scrape`.

    `_env_file=None` y los `delenv` siguen el patrón del test de arriba: sin ellos esto
    comprobaría el .env local en vez de los valores por defecto de la clase.
    """
    for variable in (
        "ADZUNA_SCRAPE_ACTIVO",
        "ADZUNA_SCRAPE_MAX_POR_RUN",
        "ADZUNA_SCRAPE_TIMEOUT",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = Settings(_env_file=None)

    assert settings.adzuna_scrape_activo is True
    assert settings.adzuna_scrape_max_por_run == 40
    assert settings.adzuna_scrape_timeout == 30.0
```

`Settings` ya está importado al principio de `tests/test_config.py`.

- [ ] **Step 2: Ejecuta el test y comprueba que falla**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL con `AttributeError: 'Settings' object has no attribute 'adzuna_scrape_activo'`.

- [ ] **Step 3: Añade los ajustes**

En `app/config.py`, justo después de `adzuna_app_key`:

```python
    # La API de Adzuna corta las descripciones a 500 caracteres, así que la ficha pública
    # se lee por HTTP para completarlas. Ver app/enrich.py y el spec del 2026-08-06.
    #
    # El interruptor existe porque esto depende del HTML de un tercero: si Adzuna cambia
    # la maquetación, se apaga desde el .env sin tocar código ni desplegar.
    adzuna_scrape_activo: bool = True
    # Techo de duración del paso, no de reintentos: de eso se encarga `intentos_scrape`.
    # A 2 s por petición son 80 segundos. Las cifras reales de ofertas nuevas por run van
    # de 0 a 27, así que el atraso nunca desplaza a las ofertas del día.
    adzuna_scrape_max_por_run: int = 40
    adzuna_scrape_timeout: float = 30.0
```

- [ ] **Step 4: Ejecuta el test y comprueba que pasa**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "Añade los ajustes del scraper de fichas de Adzuna"
```

---

## Task 9: Enganche en el pipeline

**Files:**
- Modify: `app/pipeline.py:125-155`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade a `tests/test_pipeline.py`:

```python
def test_sin_enriquecedor_el_run_funciona_igual_que_antes(sesion):
    """El parámetro es opcional a propósito: los tests y los puntos de entrada que no
    lo pasen siguen funcionando."""
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=FakeProvider([veredicto()]),
    )

    assert "_enriquecimiento" not in run.stats


def test_el_enriquecimiento_corrige_la_modalidad_antes_del_prefiltro(sesion):
    """El test que es el spec entero en una línea.

    La API de Adzuna corta a 500 caracteres, así que la modalidad se dedujo de un
    extracto que no la menciona y la oferta quedó como "desconocida". Y la modalidad
    desconocida está EXENTA de la regla de zona (app/prefilter.py:124), de modo que una
    oferta híbrida en Sevilla se cuela hasta el clasificador aunque las zonas aceptadas
    sean otras.

    Con el paso, la modalidad pasa a "hibrido", la regla de zona por fin se evalúa y la
    oferta se descarta sin gastar una llamada al LLM. El sentido de la flecha es el
    contrario del que parece: el paso no rescata ofertas del prefiltro, hace que el
    prefiltro funcione.
    """
    prepara(sesion, Preferencias(modalidades=["remoto", "hibrido"], zonas=["barcelona"]))
    provider = FakeProvider([veredicto()])
    texto_completo = (
        "Buscamos desarrollador backend para el equipo de plataforma. "
        "El puesto es para nuestra oficina de Sevilla en formato Híbrido."
    )

    run = ejecuta_run(
        sesion,
        fuentes=[
            FakeSource(
                [
                    raw(
                        "1",
                        fuente="adzuna",
                        url="https://www.adzuna.es/details/1",
                        ubicacion="Sevilla",
                        modalidad="desconocida",
                        descripcion="Buscamos desarrollador backend para el equipo…",
                        descripcion_truncada=True,
                    )
                ]
            )
        ],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=provider,
        enriquecedor=lambda url: texto_completo,
    )

    job = sesion.scalar(select(Job))
    assert job.modalidad == "hibrido"
    assert job.descripcion == texto_completo
    assert job.descripcion_truncada is False
    assert job.estado_clasificacion == "descartada_por_regla"
    assert "Sevilla" in job.motivo_regla
    assert provider.llamadas == []
    assert run.stats["_enriquecimiento"]["completadas"] == 1


def test_los_fallos_del_enriquecimiento_quedan_registrados_en_el_run(sesion):
    prepara(sesion)

    def scraper_roto(url: str) -> str:
        raise RuntimeError("timeout")

    run = ejecuta_run(
        sesion,
        fuentes=[
            FakeSource(
                [
                    raw(
                        "1",
                        fuente="adzuna",
                        url="https://www.adzuna.es/details/1",
                        descripcion_truncada=True,
                    )
                ]
            )
        ],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=FakeProvider([veredicto()]),
        enriquecedor=scraper_roto,
    )

    fallo = next(e for e in run.errores if e["tipo"] == "enriquecimiento")
    assert fallo["fuente"] == "adzuna"
    assert "timeout" in fallo["error"]
    assert run.stats["_enriquecimiento"]["fallidas"] == 1
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL con `TypeError: ejecuta_run() got an unexpected keyword argument 'enriquecedor'`.

- [ ] **Step 3: Engancha el paso**

En `app/pipeline.py`, añade el import:

```python
from app.enrich import enriquece_descripciones
```

Añade dos parámetros a la firma de `ejecuta_run()`, después de `provider`:

```python
    enriquecedor: Callable[[str], str] | None = None,
    max_scrapes: int = 40,
```

Y sustituye estas dos líneas del cuerpo:

```python
    stats = ingesta(sesion, fuentes, queries)
    errores: list[dict] = _errores_de_ingesta(stats)
```

por esto:

```python
    stats = ingesta(sesion, fuentes, queries)
    errores: list[dict] = _errores_de_ingesta(stats)

    # Va aquí y no después del prefiltro: la modalidad se deduce de la descripción, el
    # prefiltro decide por modalidad, y con el extracto de 500 caracteres de Adzuna esa
    # deducción sale "desconocida" tres de cada cuatro veces. Enriquecer después sería
    # arreglar el dato justo cuando ya no sirve para nada.
    if enriquecedor is not None:
        resumen = enriquece_descripciones(
            sesion, scraper=enriquecedor, max_por_run=max_scrapes
        )
        stats["_enriquecimiento"] = resumen.a_stats()
        errores.extend(
            _error("enriquecimiento", fuente="adzuna", job_id=job_id, error=mensaje)
            for job_id, mensaje in resumen.fallos
        )
```

Añade también a la docstring de `ejecuta_run()`, al final:

```
    `enriquecedor` completa las descripciones que Adzuna sirve truncadas antes de
    prefiltrar. Es opcional para que los tests y cualquier punto de entrada que no lo
    pase sigan funcionando igual.
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: PASS, incluidos los tres nuevos.

Run: `python -m pytest`
Expected: toda la suite en verde.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "Enriquece las descripciones de Adzuna antes de prefiltrar"
```

---

## Task 10: Cableado en la CLI y en la web

**Files:**
- Modify: `app/cli.py:52` (junto a `construye_fuentes`) y `app/cli.py:187`
- Modify: `app/web/routes_config.py:696-716`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Escribe el test que falla**

Añade a `tests/test_cli.py`:

```python
def test_construye_enriquecedor_respeta_el_interruptor():
    """Devolver None es lo mismo que no pasar el parámetro: el interruptor del .env y el
    valor por defecto de `ejecuta_run()` son la misma cosa vista desde los dos lados."""
    from app.cli import construye_enriquecedor
    from app.config import Settings

    apagado = Settings(_env_file=None, adzuna_scrape_activo=False)
    encendido = Settings(_env_file=None, adzuna_scrape_activo=True)

    assert construye_enriquecedor(apagado) is None
    assert callable(construye_enriquecedor(encendido))
```

- [ ] **Step 2: Ejecuta el test y comprueba que falla**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL con `ImportError: cannot import name 'construye_enriquecedor'`.

- [ ] **Step 3: Añade el constructor y cablea los dos puntos de entrada**

En `app/cli.py`, después de `construye_fuentes()`:

```python
def construye_enriquecedor(settings: Settings) -> Callable[[str], str] | None:
    """El lector de fichas de Adzuna, o None si está apagado.

    Devolver None es lo mismo que no pasar el parámetro a `ejecuta_run()`: el
    interruptor de configuración y el valor por defecto del pipeline son la misma cosa
    vista desde los dos lados.

    El limitador es propio y por host: `www.adzuna.es` no es `api.adzuna.com`, así que
    no compite con el de la API.
    """
    if not settings.adzuna_scrape_activo:
        return None

    from app.sources.adzuna_web import INTERVALO_SEGUNDOS, descarga_descripcion

    limitador = LimitadorPorHost(intervalo_por_defecto=INTERVALO_SEGUNDOS)

    def enriquecedor(url: str) -> str:
        return descarga_descripcion(
            url, limitador=limitador, timeout=settings.adzuna_scrape_timeout
        )

    return enriquecedor
```

Asegúrate de que `app/cli.py` tiene estos imports arriba (añade los que falten):

```python
from collections.abc import Callable

from app.limitador import LimitadorPorHost
```

En `app/cli.py:187`, añade dos argumentos a la llamada:

```python
        run = ejecuta_run(
            sesion,
            fuentes=construye_fuentes(nombres_fuentes, settings, sesion),
            queries=queries,
            provider=crear_provider(settings),
            enriquecedor=construye_enriquecedor(settings),
            max_scrapes=settings.adzuna_scrape_max_por_run,
            max_clasificaciones=settings.max_clasificaciones_por_run,
        )
```

En `app/web/routes_config.py`, cambia el import de la línea 696 y la llamada de la 709:

```python
    from app.cli import _busquedas_activas, construye_enriquecedor, construye_fuentes
```

```python
        ejecuta_run(
            sesion,
            fuentes=construye_fuentes(nombres_fuentes, settings, sesion),
            queries=queries,
            provider=crear_provider(settings),
            enriquecedor=construye_enriquecedor(settings),
            max_scrapes=settings.adzuna_scrape_max_por_run,
            max_clasificaciones=settings.max_clasificaciones_por_run,
        )
```

- [ ] **Step 4: Ejecuta la suite entera**

Run: `python -m pytest`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add app/cli.py app/web/routes_config.py tests/test_cli.py
git commit -m "Cablea el enriquecedor de Adzuna en la CLI y en el botón de la web"
```

---

## Task 11: Comprobación contra la web real

**Files:**
- Create: `tests/test_sources_adzuna_web_contrato.py`

El proyecto ya tiene el marcador `contrato` para tests contra APIs reales, excluidos por defecto (`pyproject.toml:10`). Este cambio depende del HTML de un tercero, así que merece uno: es lo que avisará de que Adzuna cambió la maquetación.

- [ ] **Step 1: Escribe el test de contrato**

Crea `tests/test_sources_adzuna_web_contrato.py`:

```python
"""Comprobación contra la web real de Adzuna. Excluida de la suite por defecto.

Ejecutar a mano cuando el enriquecimiento empiece a fallar en los runs:

    python -m pytest tests/test_sources_adzuna_web_contrato.py -m contrato -v

Si esto falla, Adzuna cambió la maquetación o el WAF, y toca mirar `_SECCION_CUERPO` y
`CABECERAS` en app/sources/adzuna_web.py.
"""

import os

import pytest

from app.sources.adzuna_web import descarga_descripcion

URL_FICHA = os.environ.get("ADZUNA_FICHA_URL", "")


@pytest.mark.contrato
@pytest.mark.skipif(not URL_FICHA, reason="define ADZUNA_FICHA_URL con una oferta viva")
def test_la_ficha_real_sigue_dando_mas_texto_que_la_api():
    texto = descarga_descripcion(URL_FICHA)

    # Medido sobre 10 ofertas reales: de 1078 a 3673 caracteres, mediana ~2100. La API
    # corta a 500, así que cualquier cosa por debajo de eso significa que ya no estamos
    # sacando el cuerpo de la oferta.
    assert len(texto) > 500
```

- [ ] **Step 2: Comprueba que la suite normal lo ignora**

Run: `python -m pytest tests/test_sources_adzuna_web_contrato.py -v`
Expected: 1 deselected (lo excluye `addopts = "-m 'not contrato'"`), 0 ejecutados.

- [ ] **Step 3: Ejecútalo de verdad, una vez**

Coge una `redirect_url` viva de la API de Adzuna y ejecuta:

```bash
ADZUNA_FICHA_URL="https://www.adzuna.es/details/<id>" python -m pytest tests/test_sources_adzuna_web_contrato.py -m contrato -v
```

Expected: PASS. Si da 403, revisa `CABECERAS`; si da el `RuntimeError` de "ni adp-body ni JobPosting", revisa `_SECCION_CUERPO`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_sources_adzuna_web_contrato.py
git commit -m "Añade el test de contrato contra la ficha real de Adzuna"
```

---

## Task 12: Primer run de verdad y comprobación del atraso

No hay código en esta tarea. Es la verificación de que el cambio hace lo que dice sobre los datos reales.

- [ ] **Step 1: Mira el estado de partida**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/app.db'); print(list(c.execute(\"select modalidad,count(*) from job where fuente='adzuna' group by 1\"))); print(list(c.execute(\"select count(*) from job where fuente='adzuna' and descripcion_truncada=1\")))"
```

Expected: 105 `desconocida` / 14 `hibrido` / 19 `remoto`, y 136 truncadas.

- [ ] **Step 2: Lanza un run**

```bash
python -m app.cli run
```

(o `docker compose run --rm app python -m app.cli run`, según cómo lo ejecutes tú.)

Expected: tarda ~80 segundos más de lo habitual (40 fichas × 2 s).

- [ ] **Step 3: Comprueba el efecto**

```bash
python -c "import sqlite3,json; c=sqlite3.connect('data/app.db'); print(json.loads(c.execute('select stats from run order by id desc limit 1').fetchone()[0]).get('_enriquecimiento')); print(list(c.execute(\"select count(*) from job where fuente='adzuna' and descripcion_truncada=1\")))"
```

Expected: `_enriquecimiento` con `completadas` cerca de 40 y `fallidas` bajo; las truncadas bajan de 136 a ~96.

Repite el run tres o cuatro días (o sube `ADZUNA_SCRAPE_MAX_POR_RUN` en el `.env` para drenarlo antes) hasta que las truncadas lleguen a 0 salvo las agotadas.

- [ ] **Step 4: Comprueba lo que el spec predice**

Después del drenaje, las ofertas de Adzuna con `modalidad = "desconocida"` deben haber bajado mucho desde 105, y **`descartada_por_regla` debe haber SUBIDO** desde 16. Eso último es el resultado correcto, no una regresión: son ofertas presenciales o fuera de zona que hasta ahora se colaban porque su modalidad era desconocida y el prefiltro no llegaba a evaluar sus reglas.

```bash
python -c "import sqlite3; c=sqlite3.connect('data/app.db'); print(list(c.execute(\"select modalidad,count(*) from job where fuente='adzuna' group by 1\"))); print(list(c.execute(\"select estado_clasificacion,count(*) from job where fuente='adzuna' group by 1\")))"
```

---

## Notas de revisión

Repasado el plan contra el spec, sección por sección:

- **Verificación previa** → recogida como comentarios en `adzuna_web.py` (Tasks 3 y 4) y como test de contrato (Task 11).
- **Dónde se scrapea** → Task 9.
- **Alcance exclusivo de Adzuna** → `FUENTE = "adzuna"` en Task 5, con su test.
- **Qué ofertas entran** → Task 5.
- **Cómo se resetea** → Task 6.
- **Coste** → los ajustes de Task 8; no requiere código.
- **Arquitectura** (dos módulos) → Tasks 3-7.
- **Modelo de datos y la trampa del NULL** → Task 1 (columna), Task 5 (`test_coge_las_filas_heredadas_con_intentos_a_null`) y Task 7 (`job.intentos_scrape or 0`).
- **Flujo del run** → Task 9.
- **Errores** → Task 7 (las tres reglas) y Task 9 (`run.errores` y `run.stats`).
- **Configuración** → Task 8, más el `INTERVALO_SEGUNDOS` de Task 4.
- **Una sola vez por oferta** → Task 6, `test_una_oferta_ya_enriquecida_no_vuelve_a_entrar`.
- **Pruebas** → todas las listadas en el spec tienen su test, con dos añadidos que aparecieron al escribir el plan: que la extracción no se trague las secciones siguientes, y que un 404 no alimente la racha de fallos.

Nombres verificados como consistentes entre tareas: `descarga_descripcion`, `extrae_descripcion`, `url_ficha`, `html_a_texto`, `CABECERAS`, `INTERVALO_SEGUNDOS`, `DescripcionNoDisponible`, `pendientes_de_enriquecer`, `enriquece_descripciones`, `ResumenEnriquecimiento.a_stats()`, `MAX_INTENTOS_SCRAPE`, `MAX_FALLOS_SEGUIDOS`, `MOTIVO_RACHA`, `construye_enriquecedor`.

`app/enrich.py` define su propio `html_a_texto` sólo en `adzuna_web.py`; el de `app/sources/remotive.py` no se toca ni se importa, y los dos nombres conviven en módulos distintos sin colisión.
