# Descripción completa de las ofertas de Adzuna — Diseño

Fecha: 2026-08-06
Estado: aprobado

## Problema

La API de Adzuna corta las descripciones a 500 caracteres y remata con puntos suspensivos.
No es configurable ni existe un campo con el texto completo; está verificado contra la API
real y documentado en `app/sources/adzuna.py:10`.

El daño no es sólo estético. `AdzunaSource._normaliza()` deduce la modalidad llamando a
`detecta_modalidad(f"{titulo} {descripcion}")` **sobre el extracto truncado**. Una oferta que
dice "para nuestra oficina de Sevilla en formato Híbrido" en el carácter 900 se guarda hoy
como `modalidad = "desconocida"`, y `aplica_prefiltro()` filtra por modalidad. Es decir: el
truncado no sólo empobrece lo que lee el clasificador, además **descarta ofertas buenas antes
de que ninguna IA las vea**.

Medido sobre la base de datos actual: **136 de las 138 ofertas de Adzuna están truncadas
(98,5 %)**. No es un caso borde, es la fuente entera.

La ficha pública de Adzuna sí publica el texto íntegro. El objetivo es leerlo por HTTP y
completar la oferta antes de prefiltrar.

## Verificación previa

Todo lo que sigue se midió contra la web real antes de diseñar nada (10 ofertas, sondas en
el scratchpad de la sesión). Sin estas cifras, media docena de decisiones de abajo serían
suposiciones.

**La ficha es accesible y trae el texto entero.** `redirect_url` ya apunta a la ficha propia
de Adzuna (`https://www.adzuna.es/details/<id>?utm_*`). 10/10 respondieron `200`, sin
challenge de JavaScript, sin cookies y sin login. El texto vive en
`<section class="adp-body">`: **1078–3673 caracteres, mediana ~2100**, frente a los 500 de la
API. Entre 2× y 7× más, y termina de forma natural.

**El JSON-LD es la red de seguridad, no la fuente principal.** Hay un
`<script type="application/ld+json">` con `JobPosting` cuyo `description` coincide
*exactamente* con `adp-body`, pero **faltaba en 1 de las 10**. `adp-body` salió en 10/10. Por
eso el orden de extracción es `adp-body` primero y JSON-LD como alternativa, y no al revés.

**`robots.txt` permite la ruta que necesitamos.** `/details/` no está en ningún `Disallow`; de
hecho Adzuna publica un `sitemap_index_details.jobs_ES.xml`. Lo prohibido es `/land/ad/` y
`/goto/ad/` — el salto al portal del anunciante, que no usamos. Vamos por la puerta permitida.

**El WAF mira las cabeceras, no el user-agent.** Medido caso por caso contra
`/details/5812188567`:

| Cabeceras | Resultado |
|---|---|
| httpx por defecto | 403 |
| `User-Agent: isThatMyNewJob/1.0 (+…)` a secas | 403 |
| `isThatMyNewJob/1.0` + `Accept` + `Accept-Language` | **200, con `adp-body`** |
| `Mozilla/5.0` pelado | 403 |
| User-agent de Chrome **sin** `Accept` | 403 |
| Chrome completo | 200 |

CloudFront rechaza por la ausencia de `Accept` y `Accept-Language`, no por el nombre del
cliente: un user-agent de Chrome sin esas cabeceras se come el mismo 403 que uno propio.

Esto resuelve una duda que sí era real. El `robots.txt` de Adzuna prohíbe explícitamente
user-agents de IA (`ClaudeBot`, `anthropic-ai`, `CCBot`, `ChatGPT-User`…), así que había que
decidir si disfrazarse de navegador. **No hace falta.** Nos identificamos con nuestro propio
nombre y un correo de contacto, mandamos las dos cabeceras que el WAF pide, y pasa igual. La
herramienta es personal, de bajo volumen y va por una ruta permitida: no hay nada que ocultar
y por tanto no se oculta nada.

## Decisiones y alternativas descartadas

### Dónde se scrapea: paso propio del pipeline

El scraping ocurre en un paso del pipeline, sobre filas `Job` ya persistidas, entre
`ingesta()` y el bucle de clasificación.

Descartadas:

- **Dentro de `AdzunaSource.search()`.** Encapsula todo en la fuente, pero scrapea también las
  ofertas que resultarán duplicadas. Las cifras reales de los últimos runs: `recibidas` 73–97
  frente a `nuevas` 0–27. Sería pagar entre tres y cuatro veces de más, cada día, para siempre.
- **Dentro de `ingesta()`, tras deduplicar.** Ahorra lo mismo que la opción elegida, pero deja
  el trabajo a merced del `rollback` por unidad de trabajo y no es reanudable.

El paso propio gana porque el scraping HTTP falla mucho más que una llamada a una API, y
trabajando sobre filas ya guardadas el fallo se reintenta al día siguiente en vez de perderse.
Además sus errores encajan sin inventar nada en el `run.errores` tipado que ya existe.

**Va antes de cargar `pendientes`, no después.** Ahí está todo el valor: el prefiltro y el
clasificador tienen que ver el texto completo y la modalidad corregida. Enriquecer después de
prefiltrar sería inútil — la oferta ya se habría descartado con datos incompletos, que es
exactamente el problema que se viene a resolver.

### Alcance: exclusivo de Adzuna, sin registro de scrapers

Una función para Adzuna, sin interfaz ni registro por fuente. Hoy sólo Adzuna marca
`descripcion_truncada = True`.

Un "enriquecedor genérico" con un registro `{"adzuna": …}` de una sola entrada no comparte
nada entre fuentes salvo el bucle y el manejo de errores: cada fuente apunta a un dominio
distinto con un HTML distinto. Cuando aparezca el segundo caso, extraer la interfaz será
trivial y estará informada por dos ejemplos reales en lugar de por uno imaginado.

### Qué ofertas entran: todas las truncadas, sin reclasificar

La condición de trabajo es "es de Adzuna y está truncada", **sin mirar
`estado_clasificacion`**. Las 136 del atraso entran en los primeros runs.

Descartadas:

- **Sólo las `pendiente`.** Deja el histórico de Adzuna juzgado con 500 caracteres para
  siempre.
- **Rellenar el atraso y reclasificar.** `Clasificacion` tiene `job_id` único, así que
  reclasificar obliga a decidir si se pisa el veredicto viejo o se guarda historial: un cambio
  de modelo de datos que no hace falta para resolver el problema planteado. Además serían 136
  llamadas al LLM de golpe contra un `max_clasificaciones_por_run` de 200.

Consecuencia asumida y explícita: **las clasificaciones ya emitidas no se rehacen.** El texto
completo queda en la base de datos y se ve en la ficha web, pero el veredicto sigue siendo el
que se emitió con el extracto. Reclasificar el atraso es una decisión aparte, fuera de este
spec.

### Corte de reintentos: contador, con los 404 como caso inmediato

Como la condición de trabajo es "está truncada", una oferta cuyo scrape falle **sigue
truncada** y volvería a entrar en cada run, para siempre. Un 404 de una oferta que Adzuna ya
borró se reintentaría a diario hasta el fin de los tiempos, y ésas son justo las que se
acumulan.

Solución: columna `intentos_scrape` en `Job`, con tope de 3 (`MAX_INTENTOS_SCRAPE` en
`app/enrich.py`), igual que `intentos_clasificacion` y su `MAX_INTENTOS` en `app/pipeline.py`.
Y el trozo barato de la alternativa por código de estado: **404 y 410 agotan de una vez**, sin
gastar tres runs en confirmar que algo borrado sigue borrado.

Descartadas: distinguir *todos* los códigos (apoya la lógica en que Adzuna devuelva siempre el
código correcto, cosa no verificada) y limitarse a un tope por run (el cupo se llena de basura
y las ofertas nuevas dejan de llegar).

### Orden de proceso: primero lo recién ingerido

Con 136 de atraso y un tope de 40 por run, ordenar por `ingerida_en` **ascendente** haría que
las viejas se comieran el cupo y las ofertas de hoy tardaran cuatro runs en tener texto
completo. Justo las que se van a clasificar en este run.

Va **descendente**: primero lo recién ingerido, el atraso se drena por detrás con lo que sobre.

## Arquitectura

Dos módulos, con una frontera clara: uno sabe de HTTP y HTML, el otro sabe de base de datos.
Ninguno importa al otro.

### `app/sources/adzuna_web.py` — el scraper

Sin ORM, sin `Session`, sin `Job`. URL entra, texto sale. Al ser puro se prueba con un HTML
fijo, sin red.

```python
class DescripcionNoDisponible(Exception):
    """La ficha ya no existe (404/410). No se reintenta."""

def descarga_descripcion(url: str, *, limitador, timeout: float = 30.0) -> str
```

Responsabilidades, en orden:

1. Normalizar la URL: quitar el query string (`?utm_medium=api&utm_source=…`) y pedir el
   `/details/<id>` pelado. Adzuna prohíbe en `robots.txt` varios patrones con query; el
   nuestro no está entre ellos, pero pedir la URL limpia evita el problema por completo.
2. Pedir con `User-Agent: isThatMyNewJob/1.0 (+contacto)`, `Accept` y `Accept-Language`.
3. Extraer `<section class="adp-body">`. Si falta, caer al `description` del `JobPosting` en
   el JSON-LD. Si faltan los dos, error.
4. Convertir el HTML a texto **conservando los saltos de línea** de `<br>`, `</p>` y `</li>`.

Sobre el punto 4: no se reutiliza `html_a_texto()` de `app/sources/remotive.py` a propósito.
Esa función colapsa todo el espacio en blanco a espacios simples, lo que convertiría una
descripción de 3600 caracteres con viñetas en un párrafo corrido ilegible en la ficha web. No
se toca `html_a_texto()` ni sus dos usos actuales: funciona para lo que hace y refactorizarlo
está fuera de alcance.

### `app/enrich.py` — el paso

```python
def enriquece_descripciones(sesion, *, scraper, max_por_run: int = 40) -> dict
```

El nombre imita el par `ingest.py` / `ingesta()` que ya existe.

Selecciona `Job.fuente == "adzuna"` **y** `descripcion_truncada` **y** no agotada, ordenado por
`ingerida_en` descendente, con límite `max_por_run`. Por cada oferta, con commit individual
como hace el bucle de clasificación:

- Éxito → escribe `descripcion`, apaga `descripcion_truncada` y **recalcula `modalidad`** con
  `detecta_modalidad(f"{titulo} {descripcion_larga}")`.
- `DescripcionNoDisponible` → `intentos_scrape = 3` (agotada de una vez).
- Cualquier otro fallo → `intentos_scrape += 1`; la oferta queda truncada y se reintenta mañana.

Sólo se reescriben esos tres campos. Salario y ubicación no se tocan: el HTML trae cifras del
estilo "Sueldo: 2.300,00 € al mes", y meterlas en `salario_min` para compararlas contra un
mínimo **anual** es exactamente el error ya documentado en `AdzunaSource._salario_publicado()`
y en `ScrappaSource._salario()`.

### Integración en `ejecuta_run`

Se inyecta como parámetro, igual que `fuentes` y `provider`:

```python
def ejecuta_run(sesion, *, fuentes, queries, provider, enriquecedor=None, ...)
```

`enriquecedor=None` salta el paso, de modo que los tests existentes de `ejecuta_run` siguen
valiendo sin tocarlos. Se cablea en los dos únicos sitios que lo llaman: `app/cli.py:187` y
`app/web/routes_config.py:709`, que construyen el enriquecedor sólo si
`settings.adzuna_scrape_activo` — el interruptor de configuración y el `None` del parámetro son
la misma cosa vista desde los dos lados.

## Modelo de datos

Una columna nueva en `Job`:

```python
intentos_scrape: Mapped[int] = mapped_column(Integer, default=0)
```

La migración la hace sola `asegura_esquema()` en `app/db.py`, sin Alembic.

**Y ahí está la trampa que hay que escribir para no pisarla.** `asegura_esquema()` añade las
columnas sin `NOT NULL` — es correcto y está documentado en `app/db.py:28`, SQLite no permite
otra cosa sobre una tabla con filas. Por tanto las 138 filas existentes quedan con
`intentos_scrape` a **NULL, no a 0**. Un `WHERE intentos_scrape < 3` en SQL **no seleccionaría
ninguna de las 136 del atraso**: `NULL < 3` evalúa a `NULL`, que no es verdadero, y la fila se
cae del filtro en silencio. El paso funcionaría con las ofertas nuevas y el atraso entero sería
invisible, sin ningún error a la vista.

La condición correcta es:

```python
or_(Job.intentos_scrape.is_(None), Job.intentos_scrape < MAX_INTENTOS_SCRAPE)
```

y hay un test dedicado exclusivamente a esto.

## Flujo del run

1. `cierra_runs_colgados()` (sin cambios)
2. `ingesta()` (sin cambios)
3. **`enriquece_descripciones()`** ← nuevo
4. Cargar `pendientes`
5. Prefiltro y clasificación (sin cambios)

## Errores

Tres reglas, ya descritas arriba: 404/410 agotan de una vez; cualquier otro fallo suma un
intento; y **cinco fallos consecutivos cortan el paso en ese run**.

El corte por racha sigue el espíritu del `CuotaAgotadaError` de `app/pipeline.py:197`. Sin él,
el día que Adzuna cambie el WAF y devuelva 403 a todo, un run quemaría 40 intentos y tres runs
bastarían para marcar **el atraso entero** como permanentemente fallido por culpa de un bloqueo
temporal.

Riesgo residual aceptado: las cinco primeras ofertas de esa racha sí gastan intento. Se asume
por simplicidad; distinguir fallos "de sitio" de fallos "de oferta" para no contarlos exigiría
la clasificación por código de estado que ya se descartó arriba.

Cada fallo va a `run.errores` con la forma común que ya existe:

```python
_error("enriquecimiento", fuente="adzuna", job_id=job.id, error=f"{type(e).__name__}: {e}")
```

El resumen va a `run.stats["_enriquecimiento"]`, en paralelo a `_totales`:

```python
{"intentadas": int, "completadas": int, "fallidas": int, "agotadas": int,
 "cortado_por": None | "racha_de_fallos"}
```

## Configuración

En `app/config.py`:

| Ajuste | Valor | Motivo |
|---|---|---|
| `adzuna_scrape_activo` | `True` | Interruptor. Si Adzuna cambia el HTML, se apaga sin desplegar código. |
| `adzuna_scrape_max_por_run` | `40` | Techo de duración del paso. |
| `adzuna_scrape_timeout` | `30.0` | |
| Intervalo entre peticiones | `2.0 s` | Es el único `Crawl-delay` que Adzuna publica en su `robots.txt` (para bingbot): su propia cifra. |

El limitador es un `LimitadorPorHost` propio. `www.adzuna.es` es un host distinto de
`api.adzuna.com`, así que no compite con el de la API. Techo por run: 40 × 2 s = 80 s.

## Pruebas

Ninguna sale a la red. El fixture es un HTML **recortado** (~4 KB con `adp-body` y JSON-LD),
no los 80 KB de la página real.

**Scraper** (`tests/test_sources_adzuna_web.py`):

- extrae el texto de `adp-body`
- cae al JSON-LD cuando falta `adp-body`
- falla limpio cuando faltan los dos
- convierte 404 y 410 en `DescripcionNoDisponible`
- quita el query string de la URL antes de pedir
- conserva los saltos de línea de `<br>`, `</p>` y `</li>`

**Paso** (`tests/test_enrich.py`):

- respeta `max_por_run`
- al completar: apaga `descripcion_truncada` y **recalcula `modalidad`**
- al fallar: suma un intento y deja la oferta truncada
- un 404 agota de una vez, sin gastar tres runs
- ignora las ofertas ya agotadas
- **selecciona las filas con `intentos_scrape` a NULL** — la regresión de la migración
- corta el paso a los cinco fallos consecutivos
- procesa primero lo más recién ingerido

**Integración** (`tests/test_pipeline.py`), el test que es este spec en una línea:

Una oferta cuyo extracto de 500 caracteres no menciona la modalidad, pero cuyo texto largo dice
"para nuestra oficina de Sevilla en formato Híbrido", con unas preferencias que sólo aceptan
híbrido. **Sin el paso, `aplica_prefiltro` la descarta. Con el paso, sobrevive y llega al
clasificador.**

## Fuera de alcance

- Reclasificar las ofertas ya clasificadas con el texto completo. Decisión aparte; arrastra la
  restricción de `job_id` único en `Clasificacion`.
- Extraer salario, ubicación o etiquetas del HTML.
- Un enriquecedor genérico con registro por fuente.
- Refactorizar `html_a_texto()` en `app/sources/remotive.py`.
- Scrapear el portal del anunciante detrás de `/land/ad/`: prohibido por el `robots.txt` de
  Adzuna.
