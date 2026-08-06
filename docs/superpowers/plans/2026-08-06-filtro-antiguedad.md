# Filtro por antigüedad en el listado — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un desplegable en el listado que oculte por defecto las ofertas publicadas hace más de tres meses, sin borrarlas ni dejar de clasificarlas.

**Architecture:** Un parámetro más en `listado()`, una condición más en la lista por comprensión que ya filtra por texto, estado y cerradas, y un `<select>` calcado del de "Ocultar las cerradas". Sin columnas, sin migración, sin tocar la ingesta ni el prefiltro.

**Tech Stack:** FastAPI + Jinja2, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-08-06-filtro-antiguedad-design.md`

---

## Contexto que el implementador necesita antes de empezar

1. **Todo el código y los comentarios están en español.** Convención: se explica el POR QUÉ y lo medido, nunca el qué. Referencia de tono: `app/sources/adzuna.py:103-119`.

2. **El listado ya filtra en Python, no en SQL.** `listado()` en `app/web/routes_ofertas.py:253` construye `visibles` con una lista por comprensión sobre `candidatas`. Añadir un filtro es añadir una condición ahí, no una consulta nueva.

3. **`crea_oferta` del conftest NO fija `publicada_en`**, así que las ofertas de prueba nacen con `None` y son visibles con cualquier umbral. Sólo dos tests fijan fecha, y son los de la Task 1.

4. **La suite debe pasar con las dos codificaciones**: `python -m pytest` y con `PYTHONUTF8=0`.

5. **El intérprete** es `C:/Python313/python.exe` si `python` no está en el PATH. Se trabaja en `D:\laragon-www\www\isThatMyNewJob`, **no** en `.claude/worktrees/`.

---

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `app/web/routes_ofertas.py` (modificar) | El parámetro, la constante, el helper y la condición. |
| `app/web/templates/ofertas.html` (modificar) | El desplegable. |
| `tests/web/test_ofertas.py` (modificar) | Fechas relativas en dos tests existentes, y los siete del filtro. |

---

## Task 1: Fechas relativas en los tests que ya existen

Va primero porque **sin esto la Task 2 rompe la suite**, y porque los dos tests son bombas de relojería aunque no se tocara nada más.

**Files:**
- Modify: `tests/web/test_ofertas.py:127-143`

- [ ] **Step 1: Comprueba el problema**

Run: `python -c "from datetime import datetime; print((datetime(2026,8,6)-datetime(2026,1,15)).days, (datetime(2026,8,6)-datetime(2026,7,15)).days)"`
Expected: `203 22`.

La oferta de enero tiene 203 días y la de julio 22. Con el filtro de la Task 2 y su defecto de 90 días, la de enero desaparecería del listado y `test_a_igual_confianza_manda_la_fecha_mas_reciente` fallaría en su `assert` de posición. La de julio pasa hoy, pero dentro de dos meses empezará a fallar sola.

- [ ] **Step 2: Sustituye las fechas absolutas por relativas**

En `tests/web/test_ofertas.py`, cambia el import de arriba:

```python
from datetime import datetime, timedelta
```

Y sustituye los dos tests completos (líneas 127-143) por:

```python
# Las fechas de los tests son relativas a hoy y no absolutas: con un filtro por
# antigüedad en el listado, una fecha fija se va quedando vieja sola y el test empieza a
# fallar un día cualquiera sin que nadie haya tocado nada.
def _hace(dias: int) -> datetime:
    return datetime.now() - timedelta(days=dias)


def test_a_igual_confianza_manda_la_fecha_mas_reciente(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Publicada hace mucho", publicada_en=_hace(60))
    crea_clasificada(titulo="Publicada hace poco", publicada_en=_hace(2))

    html = cliente.get("/").text

    assert posicion(html, "Publicada hace poco") < posicion(html, "Publicada hace mucho")


def test_una_oferta_sin_fecha_no_adelanta_a_una_reciente(cliente: TestClient, crea_clasificada):
    """Sin fecha va al final, no al principio: `None` no es 'recién publicada'."""
    crea_clasificada(titulo="Sin fecha conocida", publicada_en=None)
    crea_clasificada(titulo="Publicada hace poco", publicada_en=_hace(2))

    html = cliente.get("/").text

    assert posicion(html, "Publicada hace poco") < posicion(html, "Sin fecha conocida")
```

Los 60 días de la primera están elegidos para quedar **dentro** del defecto de 90 que llega en la Task 2: el test comprueba el ORDEN, no la visibilidad, y sacarla de la ventana lo convertiría en otro test.

- [ ] **Step 3: Ejecuta la suite**

Run: `python -m pytest tests/web/test_ofertas.py -v`
Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde. Este cambio no altera comportamiento, sólo hace los tests independientes del calendario.

- [ ] **Step 4: Commit**

```bash
git add tests/web/test_ofertas.py
git commit -m "Hace las fechas de los tests del listado relativas a hoy"
```

---

## Task 2: El filtro

**Files:**
- Modify: `app/web/routes_ofertas.py`
- Modify: `app/web/templates/ofertas.html:61-64`
- Test: `tests/web/test_ofertas.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade al final de `tests/web/test_ofertas.py`:

```python
def test_por_defecto_se_ocultan_las_publicadas_hace_mas_de_tres_meses(
    cliente: TestClient, crea_clasificada
):
    """33 ofertas de más de seis meses seguían en la lista como si fueran actuales.

    El caso real: tres de SlashMobility, de junio, agosto y octubre de 2025, que además
    estaban cerradas en origen.
    """
    crea_clasificada(titulo="Oferta rancia", publicada_en=_hace(200))
    crea_clasificada(titulo="Oferta fresca", publicada_en=_hace(10))

    html = cliente.get("/").text

    assert "Oferta fresca" in html
    assert "Oferta rancia" not in html


def test_se_pueden_pedir_las_de_cualquier_fecha(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Oferta rancia", publicada_en=_hace(200))

    html = cliente.get("/?antiguedad=todas").text

    assert "Oferta rancia" in html


def test_una_oferta_sin_fecha_se_ve_con_cualquier_umbral(
    cliente: TestClient, crea_clasificada
):
    """No saber cuándo se publicó no es motivo para esconderla.

    Mismo principio que `aplica_prefiltro()`: ante la duda, no se descarta. Son 2 de 434
    ofertas reales y todas de JSearch.
    """
    crea_clasificada(titulo="Sin fecha conocida", publicada_en=None)

    assert "Sin fecha conocida" in cliente.get("/?antiguedad=30").text


def test_el_desplegable_recuerda_lo_elegido(cliente: TestClient, crea_clasificada):
    crea_clasificada(titulo="Oferta fresca", publicada_en=_hace(10))

    html = cliente.get("/?antiguedad=todas").text

    assert re.search(r'<option value="todas"[^>]*selected', html)


def test_el_filtro_de_antiguedad_se_combina_con_el_de_cerradas(
    cliente: TestClient, crea_clasificada
):
    """Los filtros se suman, no se pisan: reciente y cerrada sigue oculta."""
    crea_clasificada(titulo="Fresca pero cerrada", publicada_en=_hace(10), cerrada=True)
    crea_clasificada(titulo="Fresca y viva", publicada_en=_hace(10))

    html = cliente.get("/").text

    assert "Fresca y viva" in html
    assert "Fresca pero cerrada" not in html


def test_un_umbral_que_no_se_entiende_no_esconde_nada(
    cliente: TestClient, crea_clasificada
):
    """El formulario sólo ofrece valores válidos, así que esto es una URL escrita a mano.

    Esconder ofertas por un parámetro que no se entiende sería lo peor que puede hacer:
    el usuario no vería ni el motivo ni las ofertas.
    """
    crea_clasificada(titulo="Oferta rancia", publicada_en=_hace(200))

    assert "Oferta rancia" in cliente.get("/?antiguedad=pepe").text
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/web/test_ofertas.py -v -k "antiguedad or umbral or fecha_se_ve or desplegable"`
Expected: FALLAN `test_por_defecto_se_ocultan_las_publicadas_hace_mas_de_tres_meses` (la rancia se ve) y `test_el_desplegable_recuerda_lo_elegido` (no existe el `<option>`). Los otros PASAN ya, porque sin filtro todo se ve: son regresiones, y que pasen desde el principio es correcto.

- [ ] **Step 3: Implementa la lógica**

En `app/web/routes_ofertas.py`, añade junto a las demás constantes de módulo (cerca de `ETIQUETAS_EJES`, sobre la línea 83):

```python
# Días de antigüedad que se muestran por defecto en el listado.
#
# Medido sobre 434 ofertas reales: 33 tenían más de seis meses y seguían en la lista como
# si fueran actuales, una de ellas de catorce meses. Pero el umbral no puede ser agresivo:
# de las 15 decisiones del usuario, 6 son sobre ofertas de más de 30 días y tres de ellas
# son candidaturas enviadas, la más antigua a 88 días. Los datos pedían 180; el usuario
# eligió 90 sabiendo que eso oculta 23 de las 99 ofertas interesantes, porque el
# desplegable las recupera con un clic.
ANTIGUEDAD_POR_DEFECTO = "90"

OPCIONES_ANTIGUEDAD: list[tuple[str, str]] = [
    ("30", "Del último mes"),
    ("90", "De los últimos 3 meses"),
    ("180", "De los últimos 6 meses"),
    ("todas", "De cualquier fecha"),
]
```

Y el helper, junto a `_coincide_texto` y `_coincide_estado`:

```python
def _es_reciente(job: Job, antiguedad: str, ahora: datetime) -> bool:
    """Si la oferta entra en la ventana de antigüedad pedida.

    Sin fecha de publicación se muestra siempre: no saber cuándo se publicó no es motivo
    para esconderla, igual que `aplica_prefiltro()` no descarta ante la duda.

    Un valor que no se entiende ("?antiguedad=pepe") también muestra todo. El formulario
    sólo ofrece valores válidos, así que llegar con otra cosa es una URL escrita a mano, y
    esconder ofertas por ello dejaría al usuario sin las ofertas y sin el motivo.
    """
    if job.publicada_en is None:
        return True
    try:
        dias = int(antiguedad)
    except ValueError:
        return True
    return (ahora - job.publicada_en).days <= dias
```

Asegúrate de que `datetime` está importado en el fichero.

Cambia la firma de `listado()` añadiendo el parámetro después de `cerradas`:

```python
    antiguedad: str = Query(default=ANTIGUEDAD_POR_DEFECTO),
```

Añade la condición a la lista por comprensión de `visibles`, después de la de `cerradas`:

```python
        # Una oferta de hace un año está casi siempre muerta y ocupa sitio. Se oculta, no
        # se descarta: la decisión es del usuario y el desplegable la devuelve.
        and _es_reciente(job, antiguedad, ahora)
```

Y justo antes de esa lista por comprensión, calcula el instante una sola vez, para que
todas las ofertas se midan contra el mismo reloj:

```python
    ahora = datetime.now()
```

Añade `antiguedad` al diccionario `filtros` del contexto, después de `"cerradas": cerradas,`:

```python
            "antiguedad": antiguedad,
```

Y `OPCIONES_ANTIGUEDAD` al contexto, junto a `"categorias"`:

```python
        "opciones_antiguedad": OPCIONES_ANTIGUEDAD,
```

- [ ] **Step 4: Añade el desplegable**

En `app/web/templates/ofertas.html`, después del `<select name="cerradas">` (línea 64) y antes del botón:

```html
    <select name="antiguedad" aria-label="Antigüedad de la oferta">
      {% for valor, etiqueta in opciones_antiguedad %}
      <option value="{{ valor }}"{% if filtros.antiguedad == valor %} selected{% endif %}>{{ etiqueta }}</option>
      {% endfor %}
    </select>
```

- [ ] **Step 5: Ejecuta los tests**

Run: `python -m pytest tests/web/test_ofertas.py -v`
Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde.

- [ ] **Step 6: Commit**

```bash
git add app/web/routes_ofertas.py app/web/templates/ofertas.html tests/web/test_ofertas.py
git commit -m "Oculta por defecto las ofertas de más de tres meses"
```

---

## Task 3: Comprobación sobre datos reales

Sin código. Verifica que el filtro hace lo que dice sobre las 434 ofertas de verdad.

- [ ] **Step 1: Cuenta lo que debería ocultarse**

```bash
python -c "import sqlite3; from datetime import datetime, timedelta; c=sqlite3.connect('data/app.db'); corte=(datetime.now()-timedelta(days=90)).isoformat(); print('ocultas:', c.execute('select count(*) from job where publicada_en is not null and publicada_en < ?', (corte,)).fetchone()[0]); print('visibles:', c.execute('select count(*) from job where publicada_en is null or publicada_en >= ?', (corte,)).fetchone()[0])"
```

Expected: unas 68 ocultas y 366 visibles, sobre el total de 434.

- [ ] **Step 2: Comprueba que las tres de SlashMobility desaparecen**

```bash
python -c "import sqlite3; from datetime import datetime, timedelta; c=sqlite3.connect('data/app.db'); corte=(datetime.now()-timedelta(days=90)).isoformat(); [print(r) for r in c.execute(\"select id, substr(publicada_en,1,10), case when publicada_en < ? then 'OCULTA' else 'visible' end, titulo from job where id in (21,22,71,73,75)\", (corte,))]"
```

Expected: las 21, 71 y 73 salen `OCULTA` —son de octubre, junio y agosto de 2025— y las 22 y 75, de marzo de 2026, también, porque tienen más de 90 días. Es el resultado correcto: las cinco son viejas.

- [ ] **Step 3: Míralo en la web**

```bash
docker compose up -d
```

Abre `http://localhost:8100/` y comprueba que el desplegable nuevo aparece con "De los últimos 3 meses" seleccionado, y que al elegir "De cualquier fecha" reaparecen las viejas.

---

## Notas de revisión

Repasado el plan contra el spec:

- **Filtrar en la web y no en la ingesta** → Task 2; ninguna tarea toca `app/ingest.py` ni `app/prefilter.py`.
- **Umbral por defecto de 3 meses, con el porqué** → la constante de la Task 2 lleva el razonamiento y las cifras.
- **Las ofertas sin fecha se muestran siempre** → `_es_reciente()` y su test.
- **Valor no reconocido = mostrar todo** → el `try/except ValueError` y su test.
- **Las siete pruebas del spec** → seis en la Task 2 más la de orden que la Task 1 conserva.

Un riesgo que el spec no anticipaba y esta revisión sí: **la Task 1 no es opcional**. `test_a_igual_confianza_manda_la_fecha_mas_reciente` usa `datetime(2026, 1, 15)`, que a día de hoy son 203 días, y el defecto de 90 lo rompería. Si alguien ejecuta la Task 2 sin la 1, verá fallar un test que no ha tocado y perderá el tiempo buscando la causa donde no está.
