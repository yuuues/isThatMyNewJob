# Créditos en el pie — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el pie de todas las vistas enlace al repositorio, a la página del autor y a
su Liberapay, y que el README diga quién lo hace.

**Architecture:** Sólo se toca `base.html`, que es la plantilla madre de las seis vistas:
el pie ya está en todas, así que no hay ruta nueva, ni vista, ni entrada en el `<nav>`.
Antes hay que estrechar un test existente que prohíbe la cadena `https://` en la
plantilla —afirmación demasiado ancha para lo que quería decir— y mudarlo al fichero
donde vive el resto del contrato de estáticos.

**Tech Stack:** Jinja2 + Pico CSS 2.1.1 (framework sin clases: se estiliza HTML
semántico, no `<div class="...">`), pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-08-06-creditos-pie-design.md`

**Cómo se corren los tests en este proyecto:**

```bash
docker compose run --rm app pytest -q
```

Ese comando sustituye el `CMD` de la imagen, así que la suite corre siempre con el
planificador apagado. Para un fichero suelto: `docker compose run --rm app pytest
tests/web/test_pie.py -v`.

---

### Task 1: Estrechar el test de "no carga nada de fuera"

Hoy `tests/web/test_tema.py` termina afirmando que `base.html` no contiene la cadena
`https://`. En cuanto el pie enlace a GitHub, ese test falla — y falla por el motivo
equivocado, porque un `<a href>` no carga nada. Se estrecha a las etiquetas que sí cargan
recursos y se muda a `test_arranque.py`, donde ya vive el contrato de los estáticos de
`base.html`.

Esta tarea va **primero y sola**: al terminarla la suite sigue verde sin haber tocado la
plantilla, así que si algo se rompe se sabe que fue el test y no el pie.

**Files:**
- Modify: `tests/web/test_tema.py:106-111` (se borra el test)
- Modify: `tests/web/test_arranque.py:115` (se añade detrás de
  `test_la_plantilla_base_enlaza_pico_antes_que_estilo`)

- [ ] **Step 1: Borrar el test viejo de `test_tema.py`**

Borrar estas seis líneas del final del fichero (líneas 106-111), incluida la línea en
blanco que las separa del test anterior:

```python
def test_el_selector_de_tema_no_carga_nada_de_fuera():
    """Los dos scripts son inline: ni CDN ni fichero nuevo en /static."""
    contenido = BASE_HTML.read_text(encoding="utf-8")

    assert "http://" not in contenido
    assert "https://" not in contenido
```

Era el único test del fichero que usaba `BASE_HTML`, así que hay que borrar también su
definición (línea 23):

```python
BASE_HTML = Path(deps.DIR_PLANTILLAS) / "base.html"
```

`ESTILO_CSS` (línea 24) se queda: la usa `test_el_selector_no_parte_la_navegacion_en_dos`.
Con `BASE_HTML` fuera, `Path` y `deps` siguen haciendo falta para esa otra constante, así
que los imports no se tocan. Confirmarlo con:

```bash
grep -n "BASE_HTML" tests/web/test_tema.py
```

Esperado: sin resultados.

- [ ] **Step 2: Añadir el test estrechado a `test_arranque.py`**

Insertarlo justo después de `test_la_plantilla_base_enlaza_pico_antes_que_estilo`, que
acaba en la línea 114, y antes de `test_la_pagina_carga_pico_desde_local`:

```python
def test_la_plantilla_base_no_carga_nada_de_fuera():
    """Ni CDN ni fuentes externas: la herramienta tiene que funcionar sin red.

    Se miran los ORÍGENES de las etiquetas que cargan recursos, no el texto del
    fichero. Buscar `https://` a pelo era lo que hacía antes este test, y desde que
    el pie enlaza a GitHub, a la página del autor y a Liberapay eso daría un falso
    positivo: un <a> no carga nada, lo sigue el usuario si quiere. Un <script src>
    o un <link href> contra un CDN sí, y son los que dejarían la herramienta
    dependiendo de la red.
    """
    contenido = BASE_HTML.read_text(encoding="utf-8")

    for etiqueta in re.findall(r"<(?:script|link|img|iframe)\b[^>]*>", contenido):
        for url in re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', etiqueta):
            assert not url.startswith(("http://", "https://", "//")), (
                f"{etiqueta.strip()} carga algo de fuera"
            )
```

`re`, `Path`, `deps` y la constante `BASE_HTML` ya están importados en ese fichero
(líneas 9, 11, 20 y 24). No hace falta añadir ningún import.

- [ ] **Step 3: Correr los dos ficheros y comprobar que siguen verdes**

```bash
docker compose run --rm app pytest tests/web/test_tema.py tests/web/test_arranque.py -q
```

Esperado: todo PASS. El test nuevo pasa contra el `base.html` actual, que sólo enlaza
`/static/pico.min.css`, `/static/estilo.css` y `/static/htmx.min.js`.

- [ ] **Step 4: Commit**

```bash
git add tests/web/test_tema.py tests/web/test_arranque.py
git commit -m "Estrecha el contrato de carga externa de la plantilla base"
```

---

### Task 2: Los tres enlaces en el pie

**Files:**
- Create: `tests/web/test_pie.py`
- Modify: `app/web/templates/base.html:81-83` (el `<footer>`)

- [ ] **Step 1: Escribir el test que falla**

Fichero nuevo `tests/web/test_pie.py`, entero:

```python
"""Créditos del pie: el repositorio, el autor y su Liberapay.

Están en `base.html` y no en una vista propia, así que aparecen en las seis
páginas sin que ninguna ruta tenga que colaborar. Lo que se comprueba aquí es que
siguen ahí y que salen con el `rel` entero: es el requisito que más fácil se
pierde en una edición futura, porque quitarlo no rompe nada visible.
"""

import re

import pytest
from fastapi.testclient import TestClient

VISTAS = ["/", "/profile", "/preferences", "/searches", "/runs"]

# Texto visible y destino de cada enlace. El texto también es contrato: "Hecho por
# Yuuu" es texto plano y el enlace es el dominio, no el nombre.
ENLACES = [
    ("Código en GitHub", "https://github.com/yuuues/isThatMyNewJob"),
    ("yuuu.es", "https://yuuu.es"),
    ("Liberapay", "https://liberapay.com/YuuuES"),
]


def _pie(html: str) -> str:
    encontrado = re.search(r"<footer\b.*?</footer>", html, flags=re.DOTALL)
    assert encontrado, "el documento no tiene <footer>"
    return encontrado.group(0)


@pytest.mark.parametrize("texto,destino", ENLACES)
def test_el_pie_lleva_los_creditos(cliente: TestClient, texto, destino):
    pie = _pie(cliente.get("/").text)

    assert f'href="{destino}"' in pie, f"falta el enlace a {destino}"
    assert texto in pie, f"falta el texto «{texto}»"


@pytest.mark.parametrize("destino", [destino for _, destino in ENLACES])
def test_los_creditos_no_filtran_la_url_de_la_vista(cliente: TestClient, destino):
    """Sin `noreferrer`, el navegador manda `localhost:8100/job/123` como `Referer`.

    Es justo lo que niega la línea de arriba del pie: los datos no salen de esta
    máquina. `noopener` es la higiene habitual de `target="_blank"` y va implícito
    en `noreferrer`, pero se escribe entero para que se lea la intención.
    """
    pie = _pie(cliente.get("/").text)

    etiqueta = re.search(rf'<a\b[^>]*href="{re.escape(destino)}"[^>]*>', pie)
    assert etiqueta, f"no hay <a> hacia {destino}"

    atributos = etiqueta.group(0)
    assert 'target="_blank"' in atributos
    assert "noopener" in atributos
    assert "noreferrer" in atributos


@pytest.mark.parametrize("vista", VISTAS)
def test_los_creditos_salen_en_todas_las_vistas(cliente: TestClient, vista):
    """Van en `base.html`, así que ninguna ruta tiene que acordarse de pasarlos."""
    respuesta = cliente.get(vista)

    assert respuesta.status_code == 200
    assert "https://github.com/yuuues/isThatMyNewJob" in _pie(respuesta.text)


def test_el_parcial_de_htmx_no_arrastra_el_pie(cliente: TestClient):
    """Los reemplazos de HTMX sólo tocan `#contenido`.

    Si el parcial trajese el pie, cada filtro del listado dejaría un juego de
    créditos más dentro de la página. Mismo control que ya hay para <nav>.
    """
    respuesta = cliente.get("/", headers={"HX-Request": "true"})

    assert respuesta.status_code == 200
    assert "<footer" not in respuesta.text
    assert "liberapay" not in respuesta.text.lower()
```

La fixture `cliente` viene de `tests/web/conftest.py` y no hay que declararla.

- [ ] **Step 2: Correr los tests y ver que fallan**

```bash
docker compose run --rm app pytest tests/web/test_pie.py -v
```

Esperado: FAIL en `test_el_pie_lleva_los_creditos` y en
`test_los_creditos_no_filtran_la_url_de_la_vista` (el pie existe pero no tiene enlaces),
y en `test_los_creditos_salen_en_todas_las_vistas`. `test_el_parcial_de_htmx_no_arrastra_el_pie`
pasa ya: es un test de regresión, no de la funcionalidad nueva.

- [ ] **Step 3: Escribir el pie**

Sustituir el `<footer>` de `app/web/templates/base.html` (líneas 81-83) por:

```html
    {# Dos párrafos y no una línea: con tres enlaces la línea única pasa de los cien
       caracteres y envuelve por donde le toque, partiendo el aviso a media frase.

       El `rel` va entero a propósito. Sin `noreferrer` el navegador manda la URL de
       la vista actual —que en `/job/{id}` lleva el identificador de la oferta— como
       cabecera `Referer` a GitHub, a yuuu.es y a Liberapay: justo lo que niega el
       párrafo de arriba.

       Son enlaces, no cargas: la regla 1 de esta plantilla sigue intacta y la
       herramienta funciona igual sin red. Por eso tampoco hay iconos, que habría
       que descargar y versionar. #}
    <footer class="container">
      <p><small class="tenue">Herramienta local. Los datos no salen de esta máquina.</small></p>
      <p>
        <small class="tenue">
          <a href="https://github.com/yuuues/isThatMyNewJob" target="_blank" rel="noopener noreferrer">Código en GitHub</a>
          ·
          Hecho por Yuuu
          ·
          <a href="https://yuuu.es" target="_blank" rel="noopener noreferrer">yuuu.es</a>
          ·
          <a href="https://liberapay.com/YuuuES" target="_blank" rel="noopener noreferrer">Liberapay</a>
        </small>
      </p>
    </footer>
```

- [ ] **Step 4: Correr los tests y ver que pasan**

```bash
docker compose run --rm app pytest tests/web/test_pie.py -v
```

Esperado: PASS todos.

- [ ] **Step 5: Correr la suite entera**

```bash
docker compose run --rm app pytest -q
```

Esperado: PASS. Interesa sobre todo que sigan verdes `tests/web/test_arranque.py` (el
test estrechado en la Task 1 es el que autoriza los `https://` de estos enlaces) y
`tests/web/test_ofertas.py`.

- [ ] **Step 6: Commit**

```bash
git add app/web/templates/base.html tests/web/test_pie.py
git commit -m "Añade los créditos al pie"
```

---

### Task 3: Apretar el hueco entre los dos párrafos del pie

Pico da a cada `<p>` un margen inferior de un espaciado tipográfico completo. Entre dos
frases sueltas de pie eso se lee como un salto de sección, no como dos líneas del mismo
bloque. Hay que **mirarlo** antes de tocar nada: si se ve bien, esta tarea se salta y se
dice que se ha saltado.

**Files:**
- Modify: `app/web/static/estilo.css` (al final, junto a la regla de `nav select`)

- [ ] **Step 1: Levantar la aplicación y mirar el pie**

```bash
docker compose up
```

Abrir <http://localhost:8100> y bajar hasta el pie. La pregunta es si las dos líneas se
leen como un bloque o si parecen dos cosas distintas.

- [ ] **Step 2: Si el hueco es excesivo, añadir la regla**

Al final de `app/web/static/estilo.css`:

```css
/* El pie son dos frases sueltas —el aviso y los créditos—, no dos bloques de texto.
   Con el margen de serie de Pico, el hueco entre ellas se lee como un salto de
   sección. Se aprieta aquí y no con un <br> en la plantilla: el salto de línea es
   presentación, y son dos afirmaciones distintas, así que en el HTML son dos <p>. */
footer p {
  margin-bottom: 0.25rem;
}

footer p:last-child {
  margin-bottom: 0;
}
```

No lleva test: el hueco es estética, no contrato. La regla de `nav select` sí lo tiene
porque sin ella la barra se parte en dos líneas, que es una consecuencia funcional.

- [ ] **Step 3: Recargar y confirmar**

Recargar <http://localhost:8100>. Esperado: las dos líneas del pie juntas, sin hueco de
sección. Parar con `Ctrl+C`.

- [ ] **Step 4: Correr la suite**

```bash
docker compose run --rm app pytest -q
```

Esperado: PASS. `tests/web/test_arranque.py` lee `estilo.css` para comprobar variables de
densidad; una regla nueva al final no le afecta, pero conviene confirmarlo.

- [ ] **Step 5: Commit (sólo si se ha tocado el CSS)**

```bash
git add app/web/static/estilo.css
git commit -m "Aprieta el hueco entre las dos líneas del pie"
```

---

### Task 4: Sección `## Autor` en el README

**Files:**
- Modify: `README.md:210` (justo antes de `## Licencia`)

- [ ] **Step 1: Añadir la sección**

Insertar entre el final de la sección `## Desarrollo` y la línea `## Licencia`:

```markdown
## Autor

Hecho por Yuuu — <https://yuuu.es>.

Si te sirve de algo, se agradece un café: <https://liberapay.com/YuuuES>.

```

No lleva enlace al repositorio: este README ya se sirve desde él, así que sería un enlace
a sí mismo.

- [ ] **Step 2: Comprobar que queda donde toca**

```bash
grep -n '^## ' README.md
```

Esperado: `## Autor` aparece justo antes de `## Licencia` y después de `## Desarrollo`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Añade la sección de autor al README"
```
