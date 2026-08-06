# El juicio de zona pasa al clasificador — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el clasificador deduzca del texto de la oferta dónde se trabaja de verdad y descarte las presenciales o híbridas fuera de las zonas del candidato, aunque el campo `ubicacion` del agregador diga otra cosa.

**Architecture:** Cuatro cambios pequeños y ninguno estructural: una regla nueva en el prompt del sistema, un sexto eje `zona` en `EjesEncaje` para que la decisión sea auditable, su etiqueta en la web, y `PROMPT_VERSION` a 3. Más un módulo `app/reclasifica.py` que devuelve las ofertas ya clasificadas a la cola para que el bucle del run existente las rehaga con el prompt nuevo y el modelo nuevo. La regla de zona del prefiltro **no se toca**.

**Tech Stack:** Python 3.13, pydantic, SQLAlchemy 2.0, FastAPI + Jinja2, pytest. Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-08-06-zona-al-clasificador-design.md`

---

## Contexto que el implementador necesita antes de empezar

1. **Comentarios: el porqué y lo medido, nunca el qué.** Referencia de tono: `app/sources/adzuna.py:103-119` y `app/db.py:16-36`. Todo el código y los comentarios están en español.

2. **Añadir un campo obligatorio a `EjesEncaje` rompe tres tests existentes** que lo construyen: `tests/test_classify.py:45`, `tests/test_pipeline.py:48` y `tests/web/test_detalle.py:71`. Hay que arreglarlos en el mismo commit o la suite queda roja.

3. **La web ya tolera ejes que no conoce.** `_ejes()` en `app/web/routes_ofertas.py:316` ordena los conocidos y añade "los que traiga de más una versión futura", pintando sólo las claves presentes. Por eso las 334 clasificaciones guardadas, que no tendrán `zona`, siguen funcionando sin migración.

4. **La suite debe pasar con las dos codificaciones**: `python -m pytest` y con `PYTHONUTF8=0`. Este repo ya tuvo un fallo que sólo aparecía con el `cp1252` de Windows.

5. **Comando de tests:** `python -m pytest` desde la raíz. Los marcados `contrato` están excluidos por defecto (`pyproject.toml:10`). El intérprete es `C:/Python313/python.exe` si `python` no está en el PATH.

---

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `app/schemas.py` (modificar) | El eje `zona` en `EjesEncaje`. |
| `app/classify.py` (modificar) | La regla 8 del prompt y `PROMPT_VERSION`. |
| `app/web/routes_ofertas.py` (modificar) | La etiqueta "Zona". |
| `app/reclasifica.py` (crear) | Devolver ofertas ya clasificadas a la cola. Sin HTTP, sin LLM. |
| `app/cli.py` (modificar) | El subcomando `reclasificar`. |
| `tests/test_schemas.py` (modificar) | El eje es obligatorio. |
| `tests/test_classify.py` (modificar) | La regla del prompt y la versión. |
| `tests/web/test_detalle.py` (modificar) | Pinta el eje nuevo; las viejas siguen pintando. |
| `tests/test_pipeline.py` (modificar) | Arreglar el constructor de `EjesEncaje`. |
| `tests/test_reclasifica.py` (crear) | El marcado. |
| `tests/test_classify_contrato.py` (crear) | Que el modelo REALMENTE deduce la ubicación. |

---

## Task 1: El eje `zona` en el esquema

**Files:**
- Modify: `app/schemas.py:75-81`
- Modify: `tests/test_classify.py:45`, `tests/test_pipeline.py:48`, `tests/web/test_detalle.py:71`
- Test: `tests/test_schemas.py`

- [ ] **Step 1: Escribe el test que falla**

Añade al final de `tests/test_schemas.py`:

```python
def test_los_ejes_incluyen_la_zona_y_es_obligatoria():
    """El eje existe para que un descarte por ubicación sea distinguible de uno técnico.

    Obligatorio y no opcional a propósito: si el modelo pudiera omitirlo, el eje se
    quedaría vacío justo en las ofertas donde la zona es dudosa, que son las únicas
    donde sirve de algo.
    """
    import pytest
    from pydantic import ValidationError

    from app.schemas import EjesEncaje

    ejes = EjesEncaje(
        tecnico="alto",
        seniority="ok",
        modalidad="remoto",
        salario="no publicado",
        sector="ok",
        zona="Barcelona, dentro de las zonas aceptadas",
    )
    assert ejes.zona == "Barcelona, dentro de las zonas aceptadas"

    with pytest.raises(ValidationError):
        EjesEncaje(
            tecnico="alto", seniority="ok", modalidad="remoto",
            salario="no publicado", sector="ok",
        )
```

- [ ] **Step 2: Ejecuta el test y comprueba que falla**

Run: `python -m pytest tests/test_schemas.py -v`
Expected: FAIL — el primer `EjesEncaje(...)` lanza `ValidationError` por el campo `zona` inesperado, o el `pytest.raises` no se cumple.

- [ ] **Step 3: Añade el campo**

En `app/schemas.py`, dentro de `class EjesEncaje`, después de `sector: str`:

```python
    # El agregador da una `ubicacion` poco fiable: medido, 11 ofertas híbridas dicen
    # "España" mientras su texto nombra Madrid, Sevilla o Alicante. El modelo deduce del
    # texto dónde se trabaja y lo explica aquí, para que un descarte por zona no sea
    # indistinguible de uno técnico. Ver la regla 8 de PROMPT_SISTEMA.
    zona: str
```

- [ ] **Step 4: Arregla los tres constructores existentes**

En `tests/test_classify.py:45-47`:

```python
        ejes=EjesEncaje(
            tecnico="alto", seniority="correcto", modalidad="remoto",
            salario="por encima del mínimo", sector="ok", zona="dentro de zona"
        ),
```

En `tests/test_pipeline.py:48-50`:

```python
        ejes=EjesEncaje(
            tecnico="alto", seniority="ok", modalidad="remoto",
            salario="no publicado", sector="ok", zona="dentro de zona"
        ),
```

En `tests/web/test_detalle.py:71-73`:

```python
        ejes=EjesEncaje(
            tecnico="Python", seniority="Senior", modalidad="Remoto",
            salario="no publicado", sector="software", zona="Barcelona"
        ),
```

**No toques el diccionario `EJES` de `tests/web/test_detalle.py:24`.** Ese representa una clasificación ya guardada en base de datos, con sus cinco claves, y tiene que seguir teniendo cinco: es lo que prueba que las 334 existentes no se rompen.

- [ ] **Step 5: Ejecuta la suite entera**

Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde con las dos.

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py tests/test_schemas.py tests/test_classify.py tests/test_pipeline.py tests/web/test_detalle.py
git commit -m "Añade el eje de zona a los ejes de encaje"
```

---

## Task 2: La regla 8 del prompt

**Files:**
- Modify: `app/classify.py:11` y `app/classify.py:15-48`
- Test: `tests/test_classify.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade al final de `tests/test_classify.py`:

```python
def test_el_prompt_manda_deducir_la_ubicacion_del_texto():
    """La regla que es todo el cambio.

    El modelo ya recibía las zonas del candidato y ya tenía orden de descartar cuando se
    incumple una preferencia, pero leía `Ubicación: España` en la ficha y no iba a buscar
    la ciudad en el texto. Nadie se lo había pedido.
    """
    from app.classify import PROMPT_SISTEMA

    minusculas = PROMPT_SISTEMA.lower()
    assert "ubicación" in minusculas
    assert "deduce" in minusculas or "deducir" in minusculas
    assert "genérico" in minusculas or "generico" in minusculas


def test_la_version_del_prompt_sube_al_cambiar_las_reglas():
    """`prompt_version` se guarda en cada clasificación y se muestra en la ficha.

    Sin subirla, dos veredictos emitidos con reglas distintas serían indistinguibles.
    """
    from app.classify import PROMPT_VERSION

    assert PROMPT_VERSION == 3
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_classify.py -v -k "prompt"`
Expected: FAIL — `assert "deduce" in ...` es falso y `PROMPT_VERSION == 3` es falso (vale 2).

- [ ] **Step 3: Añade la regla y sube la versión**

En `app/classify.py`, sustituye el bloque de `PROMPT_VERSION` (línea 7-11) por:

```python
# v2: la v1 producía 111 "revisar" de 179 ofertas, 93 de ellas con confianza baja.
# Dos causas medidas: la regla del salario se disparaba en casi todas las ofertas (en
# España casi nadie lo publica) y las descripciones truncadas llevaban al modelo a
# abstenerse en lugar de juzgar con lo visible.
#
# v3: la regla 8. El campo `ubicacion` del agregador no es de fiar y falla siempre en la
# misma dirección, dejando pasar: medido, 11 ofertas híbridas dicen "España" mientras su
# texto nombra Madrid, Sevilla, Alicante, Burgos o Baleares, y otras 6 traen una provincia
# que el texto contradice. El prefiltro no puede arreglarlo porque exime el ámbito
# nacional a propósito, así que el juicio fino pasa aquí.
PROMPT_VERSION = 3
```

Y añade al final de `PROMPT_SISTEMA`, después de la regla 7 y antes de las comillas de cierre:

```
8. El campo `Ubicación` lo da el agregador y a menudo es genérico ("España", "Remote") o \
directamente erróneo. Deduce del TEXTO dónde se trabaja de verdad. Si el puesto es \
presencial o híbrido y la ubicación real cae fuera de las zonas del candidato, la \
categoría es "descartar" aunque el campo `Ubicación` diga otra cosa. En el eje `zona` \
escribe qué ubicación has deducido y de dónde la has sacado; si el texto no da ninguna \
pista, dilo y no descartes por ello.
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_classify.py -v`
Expected: PASS.

Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde.

- [ ] **Step 5: Commit**

```bash
git add app/classify.py tests/test_classify.py
git commit -m "Pide al clasificador que deduzca la ubicación real del texto"
```

---

## Task 3: La etiqueta "Zona" en la ficha

**Files:**
- Modify: `app/web/routes_ofertas.py:83-89`
- Test: `tests/web/test_detalle.py`

- [ ] **Step 1: Escribe los tests que fallan**

Añade al final de `tests/web/test_detalle.py`:

```python
def test_la_ficha_pinta_el_eje_de_zona(cliente: TestClient, sesion, crea_clasificada):
    """El eje sirve para que el descarte por ubicación sea auditable, y para eso hay que
    verlo."""
    from app.models import Clasificacion

    oferta = crea_clasificada()
    fila = sesion.execute(
        select(Clasificacion).where(Clasificacion.job_id == oferta.id)
    ).scalar_one()
    fila.ejes = {**EJES, "zona": "Alicante, fuera de las zonas aceptadas"}
    sesion.commit()

    html = cliente.get(f"/job/{oferta.id}").text

    assert "Zona" in html
    assert "Alicante, fuera de las zonas aceptadas" in html


def test_una_clasificacion_sin_eje_de_zona_sigue_pintando(
    cliente: TestClient, crea_clasificada
):
    """La regresión que protege a las 334 clasificaciones ya guardadas.

    Ninguna tiene el eje `zona`, porque se emitieron antes de que existiera. `_ejes()`
    sólo pinta las claves presentes, así que deben seguir mostrando sus cinco filas. Si
    alguien lo cambiara por un acceso directo al campo, esto se pondría rojo.
    """
    oferta = crea_clasificada()

    respuesta = cliente.get(f"/job/{oferta.id}")

    assert respuesta.status_code == 200
    for eje in EJES.values():
        assert eje in respuesta.text
```

- [ ] **Step 2: Ejecuta los tests y comprueba que el primero falla**

Run: `python -m pytest tests/web/test_detalle.py -v -k "zona"`
Expected: `test_la_ficha_pinta_el_eje_de_zona` FALLA porque la etiqueta se pinta como la clave cruda `zona` y no como `Zona`. El segundo test PASA ya: es una regresión, no una funcionalidad nueva.

- [ ] **Step 3: Añade la etiqueta**

En `app/web/routes_ofertas.py`, dentro de `ETIQUETAS_EJES`, después de `"sector": "Sector",`:

```python
    "zona": "Zona",
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/web/test_detalle.py -v`
Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde.

- [ ] **Step 5: Commit**

```bash
git add app/web/routes_ofertas.py tests/web/test_detalle.py
git commit -m "Muestra el eje de zona en la ficha de la oferta"
```

---

## Task 4: Devolver las ofertas a la cola

**Files:**
- Create: `app/reclasifica.py`
- Test: `tests/test_reclasifica.py`

- [ ] **Step 1: Escribe los tests que fallan**

Crea `tests/test_reclasifica.py`:

```python
from sqlalchemy import select

from app.models import Clasificacion, Decision, Job
from app.reclasifica import marca_para_reclasificar


def crea_job(sesion, external_id="1", **kwargs) -> Job:
    base = dict(
        fuente="adzuna",
        external_id=external_id,
        url=f"https://www.adzuna.es/details/{external_id}",
        titulo="Backend Developer",
        empresa="Empresa",
        descripcion="Descripción completa de la oferta.",
        hash_dedup=f"hash-{external_id}",
        estado_clasificacion="clasificada",
    )
    base.update(kwargs)
    job = Job(**base)
    sesion.add(job)
    sesion.commit()
    return job


def crea_clasificacion(sesion, job) -> None:
    sesion.add(
        Clasificacion(
            job_id=job.id,
            categoria="aplicar_ya",
            confianza="alta",
            razonamiento="Juzgada con el prompt v2 y flash.",
            ejes={"tecnico": "ok", "seniority": "ok", "modalidad": "remoto",
                  "salario": "no publicado", "sector": "ok"},
            modelo="deepseek-v4-flash",
            prompt_version=2,
        )
    )
    sesion.commit()


def test_devuelve_la_oferta_a_la_cola_y_borra_el_veredicto(sesion):
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)

    marcadas = marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert marcadas == 1
    assert job.estado_clasificacion == "pendiente"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is None


def test_deja_los_intentos_de_clasificacion_a_cero(sesion):
    """Sin esto, devolver la oferta a la cola no la reabre: la entierra.

    Una oferta que agotó los tres intentos vuelve a "pendiente" y el bucle de
    `pipeline.py` la manda al estado terminal nada más sacarla, sin clasificarla ni una
    vez. `reintentar()` en app/web/routes_runs.py documenta la misma trampa.
    """
    job = crea_job(sesion, "1", intentos_clasificacion=3)
    crea_clasificacion(sesion, job)

    marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert job.intentos_clasificacion == 0


def test_limpia_el_motivo_de_regla(sesion):
    job = crea_job(
        sesion, "1", estado_clasificacion="descartada_por_regla",
        motivo_regla="zona fuera de rango: Madrid",
    )

    marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert job.motivo_regla is None
    assert job.estado_clasificacion == "pendiente"


def test_salta_las_ofertas_que_el_usuario_ya_decidio(sesion):
    """Reopinar sobre algo que ya cerró a mano no aporta nada y la reabre en la lista."""
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="Me presenté"))
    sesion.commit()

    marcadas = marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert marcadas == 0
    assert job.estado_clasificacion == "clasificada"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None


def test_puede_incluir_las_decididas_si_se_pide(sesion):
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="Me presenté"))
    sesion.commit()

    marcadas = marca_para_reclasificar(sesion, saltar_decididas=False)

    sesion.refresh(job)
    assert marcadas == 1
    assert job.estado_clasificacion == "pendiente"


def test_no_toca_una_oferta_que_nunca_se_clasifico(sesion):
    """Ya está en la cola: marcarla otra vez no aporta nada y falsearía el recuento."""
    crea_job(sesion, "1", estado_clasificacion="pendiente")

    assert marca_para_reclasificar(sesion) == 0
```

- [ ] **Step 2: Ejecuta los tests y comprueba que fallan**

Run: `python -m pytest tests/test_reclasifica.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.reclasifica'`.

- [ ] **Step 3: Escribe el módulo**

Crea `app/reclasifica.py`:

```python
"""Devuelve a la cola ofertas ya juzgadas, para rehacerlas con reglas o modelo nuevos.

Este módulo NO clasifica: marca y sale. El trabajo lo hace el bucle de `ejecuta_run()`,
que ya sabe hacerlo, respeta el tope por run y registra los fallos. Duplicar aquí la
llamada al modelo sería mantener dos caminos para lo mismo.

Existe porque el veredicto guardado no dice sólo "encaja o no": dice con qué prompt y con
qué modelo se decidió eso, y esos dos cambian. `oferta.html` muestra ambos justamente para
que se pueda saber cuándo un veredicto se ha quedado viejo.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Clasificacion, Decision, Job


def marca_para_reclasificar(sesion: Session, *, saltar_decididas: bool = True) -> int:
    """Devuelve a "pendiente" las ofertas ya clasificadas. Da cuántas marcó.

    Las que el usuario ya decidió a mano se saltan por defecto: su veredicto ya no manda
    nada, y reabrirlas sólo las devolvería a la lista de revisión.

    `intentos_clasificacion` vuelve a cero por el mismo motivo que en `reintentar()` de
    app/web/routes_runs.py: sin eso el pipeline ve la oferta agotada nada más sacarla de
    la cola y la devuelve al estado terminal sin llegar a intentarlo.
    """
    decididas = {d.job_id for d in sesion.scalars(select(Decision)).all()}

    marcadas = 0
    for job in sesion.scalars(
        select(Job).where(Job.estado_clasificacion != "pendiente")
    ).all():
        if saltar_decididas and job.id in decididas:
            continue

        sesion.execute(delete(Clasificacion).where(Clasificacion.job_id == job.id))
        job.estado_clasificacion = "pendiente"
        job.motivo_regla = None
        job.intentos_clasificacion = 0
        marcadas += 1

    sesion.commit()
    return marcadas
```

- [ ] **Step 4: Ejecuta los tests y comprueba que pasan**

Run: `python -m pytest tests/test_reclasifica.py -v`
Expected: PASS, 6 tests.

Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`

- [ ] **Step 5: Commit**

```bash
git add app/reclasifica.py tests/test_reclasifica.py
git commit -m "Devuelve las ofertas juzgadas a la cola para rehacerlas"
```

---

## Task 5: El comando `reclasificar`

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Escribe el test que falla**

Añade al final de `tests/test_cli.py`:

```python
def test_el_comando_reclasificar_marca_y_no_clasifica(monkeypatch, tmp_path, capsys):
    """Marca y sale. Clasificar es trabajo del run, que ya respeta el tope y los fallos."""
    from app import cli
    from app.db import crear_engine, crear_sesion, crear_tablas
    from app.models import Clasificacion, Job

    ruta = tmp_path / "app.db"
    monkeypatch.setenv("RUTA_BD", str(ruta))

    engine = crear_engine(str(ruta))
    crear_tablas(engine)
    with crear_sesion(engine) as s:
        job = Job(
            fuente="adzuna", external_id="1", url="https://example.com/1",
            titulo="Backend", empresa="Empresa", descripcion="Texto",
            hash_dedup="h1", estado_clasificacion="clasificada",
        )
        s.add(job)
        s.commit()
        s.add(
            Clasificacion(
                job_id=job.id, categoria="aplicar_ya", confianza="alta",
                razonamiento="…", ejes={}, modelo="deepseek-v4-flash", prompt_version=2,
            )
        )
        s.commit()

    assert cli.main(["reclasificar"]) == 0

    salida = capsys.readouterr().out
    assert "1" in salida

    with crear_sesion(crear_engine(str(ruta))) as s:
        assert s.get(Job, 1).estado_clasificacion == "pendiente"
```

- [ ] **Step 2: Ejecuta el test y comprueba que falla**

Run: `python -m pytest tests/test_cli.py -v -k reclasificar`
Expected: FAIL — `argparse` no conoce el subcomando y `main` sale con código 2.

- [ ] **Step 3: Añade el comando**

En `app/cli.py`, después de `comando_run()`:

```python
def comando_reclasificar(args) -> int:
    """Devuelve a la cola las ofertas ya juzgadas. No las clasifica.

    Se separa del run a propósito: marcar es instantáneo y reversible sólo hacia
    adelante, mientras que clasificar cuesta dinero y tiempo. Verlas marcadas antes de
    lanzar el run da la oportunidad de arrepentirse.
    """
    from app.reclasifica import marca_para_reclasificar

    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)

    with crear_sesion(engine) as sesion:
        marcadas = marca_para_reclasificar(
            sesion, saltar_decididas=not args.incluir_decididas
        )

    tope = settings.max_clasificaciones_por_run
    print(f"{marcadas} ofertas devueltas a la cola.")
    if marcadas > tope:
        print(
            f"El tope por run es {tope}, así que harán falta "
            f"{-(-marcadas // tope)} runs, o subir MAX_CLASIFICACIONES_POR_RUN."
        )
    print("Lanza 'python -m app.cli run' para que se clasifiquen.")
    return 0
```

Y en `main()`, después del bloque de `p_run`:

```python
    p_recl = sub.add_parser(
        "reclasificar", help="Devuelve a la cola las ofertas ya juzgadas"
    )
    p_recl.add_argument(
        "--incluir-decididas",
        action="store_true",
        help="Rehace también las ofertas sobre las que ya decidiste a mano",
    )
    p_recl.set_defaults(func=comando_reclasificar)
```

- [ ] **Step 4: Ejecuta los tests**

Run: `python -m pytest tests/test_cli.py -v`
Run: `python -m pytest`
Run: `PYTHONUTF8=0 python -m pytest`
Expected: todo verde.

- [ ] **Step 5: Commit**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "Añade el comando para devolver las ofertas a la cola"
```

---

## Task 6: El test de contrato

**Files:**
- Create: `tests/test_classify_contrato.py`

Éste es el único test que comprueba que el cambio hace lo que promete. Los demás verifican que el campo existe y se guarda; éste, que el modelo lo usa.

- [ ] **Step 1: Escribe el test de contrato**

Crea `tests/test_classify_contrato.py`:

```python
"""Comprueba contra el LLM real que la regla 8 funciona. Excluido de la suite.

El resto de tests del clasificador usan un proveedor falso, así que seguirían verdes
aunque el modelo ignorase por completo la instrucción de deducir la ubicación. Éste es el
único que se enteraría.

Reproduce el caso que motivó el cambio: el job 87 de la base real, cuyo campo `ubicacion`
dice "España" mientras su descripción dice "Híbrido (presencial en Alicante)". Con zonas
["barcelona"], la respuesta correcta es descartar.

Ejecutar:

    python -m pytest tests/test_classify_contrato.py -m contrato -v
"""

import pytest

from app.classify import clasifica
from app.config import get_settings
from app.llm.factory import crear_provider
from app.schemas import PerfilCandidato, Preferencias, RawJob, SkillPerfil

OFERTA = RawJob(
    fuente="adzuna",
    external_id="contrato-87",
    url="https://www.adzuna.es/details/0",
    titulo="Programador/a PHP (Híbrido)",
    empresa="Empresa de ejemplo",
    # El campo que da el agregador, genérico y por tanto inútil para filtrar.
    ubicacion="España",
    modalidad="hibrido",
    descripcion=(
        "Buscamos Programador/a PHP para incorporarse a nuestro equipo de desarrollo.\n\n"
        "Requisitos:\n"
        "- Experiencia demostrable con PHP y Laravel\n"
        "- Conocimientos de MySQL y control de versiones con Git\n\n"
        "Condiciones:\n"
        "– Contrato indefinido a jornada completa\n"
        "– Híbrido (presencial en Alicante)\n"
        "– Incorporación inmediata\n"
    ),
)

PERFIL = PerfilCandidato(
    anios_experiencia=8,
    titulo_actual="Backend Developer",
    roles=["Backend Developer"],
    skills=[SkillPerfil(nombre="PHP", nivel="alto", anios=8.0)],
    resumen="Backend PHP con ocho años de experiencia.",
)

PREFERENCIAS = Preferencias(modalidades=["remoto", "hibrido"], zonas=["barcelona"])


@pytest.mark.contrato
def test_el_modelo_descarta_por_una_ubicacion_que_solo_esta_en_el_texto():
    veredicto = clasifica(
        OFERTA,
        perfil=PERFIL,
        prefs=PREFERENCIAS,
        ejemplos=[],
        provider=crear_provider(get_settings()),
    )

    assert veredicto.categoria == "descartar", (
        f"El modelo dijo {veredicto.categoria!r}. Razonamiento: {veredicto.razonamiento} "
        f"Eje de zona: {veredicto.ejes.zona}"
    )
    assert "alicante" in veredicto.ejes.zona.lower()
```

- [ ] **Step 2: Comprueba que la suite normal lo ignora**

Run: `python -m pytest tests/test_classify_contrato.py -v`
Expected: `1 deselected`, 0 ejecutados.

- [ ] **Step 3: Commit**

```bash
git add tests/test_classify_contrato.py
git commit -m "Añade el test de contrato de la regla de ubicación"
```

---

## Task 7: Ejecutar el contrato de verdad, antes de gastar

No hay código en esta tarea. Es la puerta que decide si el resto del plan tiene sentido.

- [ ] **Step 1: Ejecuta el test de contrato contra el modelo real**

```bash
python -m pytest tests/test_classify_contrato.py -m contrato -v
```

Expected: PASS. El modelo configurado es `deepseek-v4-pro` y la llamada cuesta unos $0,0013.

- [ ] **Step 2: Si falla, PARA y repórtalo**

Un fallo aquí significa que la regla 8 no basta para que el modelo deduzca la ubicación, y entonces **no hay que ejecutar la Task 8**: rehacer 334 clasificaciones con un prompt que no funciona cuesta $0,45 y no arregla nada.

El mensaje del assert incluye el razonamiento y el eje de zona, que es por dónde empezar a mirar. Opciones si falla: endurecer la redacción de la regla 8, o moverla más arriba en la lista de reglas, donde el modelo la pondere más.

---

## Task 8: Rehacer las clasificaciones

Tampoco hay código. Es la operación sobre datos reales, y sólo se ejecuta si la Task 7 pasó.

- [ ] **Step 1: Copia de seguridad**

```bash
cp data/app.db "data/app.db.bak-reclasificacion"
```

- [ ] **Step 2: Mira el estado de partida**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/app.db'); print(list(c.execute('select categoria,count(*) from classification group by 1'))); print(list(c.execute('select modelo,count(*) from classification group by 1'))); print(list(c.execute('select prompt_version,count(*) from classification group by 1')))"
```

Expected: el reparto por categoría, 327 de `deepseek-v4-flash` más 5 de `deepseek-v4-pro`, y las 334 con `prompt_version` 2.

- [ ] **Step 3: Marca**

```bash
python -m app.cli reclasificar
```

Expected: unas 320 ofertas devueltas a la cola —las 334 menos las 15 decididas— y el aviso de que harán falta dos runs con el tope de 200.

- [ ] **Step 4: Sube el tope y lanza el run**

Para hacerlo de una vez, en el `.env`:

```
MAX_CLASIFICACIONES_POR_RUN=400
```

```bash
python -m app.cli run
```

- [ ] **Step 5: Comprueba el efecto**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/app.db'); print(list(c.execute('select categoria,count(*) from classification group by 1'))); print(list(c.execute('select prompt_version,count(*) from classification group by 1'))); print(list(c.execute(\"select count(*) from classification where ejes like '%zona%'\")))"
```

Expected: todas con `prompt_version` 3 salvo las 15 decididas, que conservan la 2, y el eje `zona` presente en las nuevas.

**Lo que se espera ver, y no es un retroceso:** un trasvase claro de `aplicar_ya` a `revisar`. Pro es más conservador que flash —en la muestra medida, 8 de cada 10 desacuerdos bajaban la categoría— y ese sesgo es el que coincidió con el criterio del usuario en 6 de 7 casos.

- [ ] **Step 6: Comprueba los casos que motivaron el cambio**

```bash
python -c "import sqlite3; c=sqlite3.connect('data/app.db'); [print(r) for r in c.execute(\"select j.id, j.ubicacion, j.modalidad, cl.categoria, cl.ejes from job j join classification cl on cl.job_id=j.id where j.id in (12,17,18,20,92,103,220,302,309)\")]"
```

Son las ofertas híbridas cuyo proveedor dice "España" mientras el texto nombra Madrid, Sevilla, Baleares o Burgos. Con las zonas en `barcelona` y `cataluña`, lo esperable es que la mayoría pase a `descartar` y que su eje `zona` nombre la ciudad real. El job 87 no está en la lista porque tiene decisión tuya y se salta.

---

## Notas de revisión

Repasado el plan contra el spec:

- **Problema y medición** → recogidos como comentarios en `app/schemas.py` (Task 1) y `app/classify.py` (Task 2), y como caso reproducido en el test de contrato (Task 6).
- **Quién juzga la zona: prefiltro Y clasificador** → la Task 2 añade el juicio del modelo; ninguna tarea toca `app/prefilter.py`, que es la decisión tomada.
- **La ubicación la deduce el LLM, no un extractor local** → ninguna tarea crea `detecta_ubicacion()`. El porqué queda en el spec.
- **El eje** → Tasks 1 y 3, con la regresión de las clasificaciones viejas en `test_una_clasificacion_sin_eje_de_zona_sigue_pintando`.
- **`PROMPT_VERSION` 2 → 3** → Task 2, con test.
- **El rehacer** → Tasks 4, 5 y 8, con `intentos_clasificacion = 0` cubierto por test propio.
- **Errores** → no hay caminos nuevos; el riesgo real (que la regla no funcione) es la Task 7, que es una puerta explícita antes de gastar.
- **Pruebas** → todas las listadas en el spec tienen tarea.

Nombres verificados como consistentes entre tareas: `EjesEncaje.zona`, `PROMPT_SISTEMA`, `PROMPT_VERSION`, `ETIQUETAS_EJES`, `marca_para_reclasificar`, `saltar_decididas`, `comando_reclasificar`.

Un detalle que el spec no fijaba y aquí se decide: `marca_para_reclasificar()` selecciona por `estado_clasificacion != "pendiente"` en vez de por "tiene clasificación". Así entran también las `descartada_por_regla`, que no tienen fila en `classification` pero cuyo descarte pudo decidirse con la ubicación mala, y quedan fuera las que ya están en la cola.
