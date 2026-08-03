# Correcciones del pipeline — Defectos y huecos del spec

> **For agentic workers:** cada entrada define un fallo con su reproducción y el
> comportamiento esperado. Escribe primero un test que reproduzca el fallo, compruébalo
> en rojo, arréglalo, compruébalo en verde. Steps con checkbox (`- [ ]`).

**Goal:** dejar el pipeline conforme al spec, corrigiendo los 7 defectos encontrados en
la revisión y cerrando los 5 requisitos sin implementar.

**Por qué este documento no lleva código:** el plan anterior dictaba implementaciones
literales que nunca se ejecutaron, y los defectos de abajo entraron por ahí. Aquí se
especifica el comportamiento y la reproducción; el código lo escribe y lo verifica quien
implementa.

**Spec:** [2026-08-03-clasificador-ofertas-design.md](../specs/2026-08-03-clasificador-ofertas-design.md)
**Plan original:** [2026-08-03-pipeline.md](2026-08-03-pipeline.md)

**Regla general para todos los arreglos:** cada uno necesita un test de regresión que
falle antes del arreglo. Un arreglo sin test que lo demuestre no cuenta como hecho.
Al terminar, la suite completa debe seguir verde: `docker compose run --rm app pytest`.

---

## Grupo A — Ingesta y fuentes

### A1. La deduplicación por `(fuente, external_id)` no se consulta *(gravedad alta)*

**Reproducción:** una oferta ya ingerida vuelve a llegar de la misma fuente con el título
ligeramente cambiado. Mismo `external_id`, `hash_dedup` distinto. Pasa el filtro por hash,
el `flush()` choca contra `UniqueConstraint("fuente", "external_id")`, el `except` genérico
hace `rollback()` y se pierde la ingesta completa de esa fuente en ese run. Medido: 3
ofertas de las que una colisiona → 0 filas persistidas.

**Esperado:** `ingesta()` comprueba las dos claves antes de insertar — `hash_dedup` y
`(fuente, external_id)` — y trata la colisión como duplicado, no como error. El spec exige
ambas claves.

**Tests requeridos:**
- Una oferta con mismo `(fuente, external_id)` y hash distinto cuenta como duplicada y no se inserta.
- Las ofertas nuevas del mismo lote sí se persisten pese a esa colisión.

### A2. Las estadísticas cuentan filas que el rollback borró *(gravedad alta)*

**Reproducción:** `resumen["nuevas"]` se incrementa antes del commit y no se revierte en el
`except`. Medido: `stats` reportó `nuevas=2` con 0 filas en la tabla.

**Esperado:** las cifras de `stats` reflejan lo realmente persistido. Si hay rollback, los
contadores de lo revertido vuelven a cero. Estas cifras acaban en `run.stats` y son lo que
`/runs` mostrará.

**Test requerido:** tras un fallo que provoque rollback, `stats["<fuente>"]["nuevas"]`
coincide con el número real de filas en la tabla.

### A3. Un fallo en la segunda búsqueda borra lo ingerido por la primera *(gravedad alta)*

**Reproducción:** el `try/except` y el único `commit()` envuelven el bucle completo de
búsquedas de una fuente. Si la búsqueda 1 va bien y la 2 falla, el rollback se lleva las
dos. Medido: `recibidas=2, nuevas=2` reportado, 0 jobs persistidos.

**Esperado:** el fallo de una búsqueda no afecta a lo ya ingerido por otra búsqueda de la
misma fuente. El aislamiento debe ser por unidad de trabajo, no por fuente entera.

**Test requerido:** con dos búsquedas sobre una fuente donde la segunda lanza excepción,
lo ingerido por la primera sobrevive y el error queda registrado.

### A4. Rate limiting y descargas redundantes del feed *(hueco del spec)*

**Contexto verificado contra las APIs reales:** Remotive y Arbeitnow **ignoran el texto de
búsqueda** y devuelven siempre el feed completo; el filtrado es local. `ingesta()` llama a
`search()` una vez por búsqueda guardada, así que con 3 búsquedas activas Remotive recibe
3 descargas idénticas por run. Su aviso legal pide un máximo de ~4 peticiones al día.

**Esperado, dos cosas:**

1. Una fuente que no filtra en servidor se descarga **una sola vez por run** y se filtra
   en local contra todas las búsquedas. La interfaz `JobSource` debe permitir distinguir
   ambos comportamientos de forma explícita, no por convención implícita.
2. Un intervalo mínimo entre peticiones a un mismo host, configurable, que el propio código
   haga cumplir. El spec lo exige en su sección de errores.

**Tests requeridos:**
- Con 3 búsquedas guardadas y una fuente que no filtra en servidor, se hace 1 petición HTTP, no 3.
- Con una fuente que sí filtra en servidor (Adzuna), se hace 1 petición por búsqueda.
- Ninguna oferta se pierde por el cambio: el filtrado local sigue aplicando todas las búsquedas.
- El limitador espera lo configurado entre dos peticiones consecutivas (sin `sleep` real en el test).

### A5. `AdzunaSource` no llama a `raise_for_status()` *(gravedad media)*

**Reproducción:** un 401 o un 429 con cuerpo JSON válido pasa el manejo de error existente
y se procesa como si fuera una respuesta correcta, devolviendo cero ofertas en silencio.
El manejo actual sólo cubre el caso de cuerpo no-JSON.

**Esperado:** un código de error HTTP se propaga como error, tanto si el cuerpo es JSON como
si es HTML.

**Test requerido:** un 429 con cuerpo JSON lanza excepción.

**Ficheros del grupo A:** `app/sources/*.py`, `app/ingest.py`, y el módulo nuevo que
necesites para el limitador. Tests: `tests/test_ingest.py`, `tests/test_sources_*.py`,
y el de tu módulo nuevo.

---

## Grupo B — Pipeline y resiliencia

### B1. Las ofertas agotadas se pierden en silencio *(gravedad alta)*

**Reproducción:** `MAX_INTENTOS = 3` excluye de la cola las ofertas con 3 intentos fallidos
pero nunca les cambia el estado. Medido con 4 runs consecutivos fallando el LLM: los runs
1-3 dejan `intentos=1,2,3` y aparecen en `run.errores`; a partir del run 4 la oferta queda
en `estado_clasificacion="pendiente"` de forma permanente, ningún run la recoge, y deja de
aparecer en `run.errores`. Desaparece sin rastro.

**Esperado:** una oferta que agota los intentos pasa a un estado terminal distinguible
(`app/models.py` ya documenta `error` en el comentario del modelo, pero nadie lo asigna),
de modo que sea visible en `/runs` y separable de una oferta simplemente encolada. El spec
exige que las ofertas que fallan no se pierdan.

**Tests requeridos:**
- Tras agotar los intentos, el estado es terminal y distinguible de `pendiente`.
- Esa oferta no vuelve a consumir llamadas al LLM en runs posteriores.
- Sigue siendo localizable: consultable por estado.

### B2. Sin reintentos ni backoff *(hueco del spec)*

**Esperado:** el spec pide 2 reintentos con backoff exponencial ante error, timeout o JSON
inválido del LLM, y sólo después aplazar la oferta al run siguiente. Hoy no hay reintento
alguno: un timeout puntual cuesta un día entero de retraso.

**Tests requeridos:**
- Un fallo transitorio seguido de éxito clasifica la oferta dentro del mismo run.
- Tres fallos seguidos consumen exactamente los reintentos previstos y no más.
- El test no debe dormir de verdad: la espera tiene que ser inyectable o simulable.

### B3. Sin circuit breaker por cuota agotada *(hueco del spec)*

**Esperado:** cuando el proveedor indica cuota agotada o rate limit, se cortan las llamadas,
se cierra el run y la cola queda para el día siguiente. Hoy el bucle sigue llamando oferta
a oferta hasta agotar `max_clasificaciones`.

Necesitas decidir cómo se reconoce esa condición. Los providers actuales propagan
`httpx.HTTPStatusError` (DeepSeek) y excepciones del SDK (Gemini). **Define una excepción
propia del dominio y haz que cada provider la levante ante 429 o equivalente**, en lugar de
que el pipeline inspeccione excepciones de terceros — eso acoplaría el pipeline a las
librerías, que es justo lo que la interfaz `LLMProvider` existe para evitar.

**Tests requeridos:**
- Ante la señal de cuota agotada, el run para de llamar al proveedor inmediatamente.
- Las ofertas no procesadas quedan en la cola, sin gastar intentos.
- El run se cierra registrando el motivo.
- DeepSeek convierte un 429 en la excepción del dominio.

### B4. `run.errores` mezcla dos formas incompatibles *(gravedad baja)*

**Reproducción:** los fallos de fuente se registran como `{"fuente", "error"}` y los de
clasificación como `{"job_id", "error"}`. Quien lea `e["fuente"]` sobre un error de
clasificación lanza `KeyError`. El spec pide que `/runs` los muestre.

**Esperado:** una única forma con campos consistentes, que distinga el tipo de error sin
que el consumidor tenga que adivinar qué claves existen.

**Test requerido:** un run con un fallo de fuente y otro de clasificación produce entradas
con la misma forma, ambas legibles con el mismo código.

**Ficheros del grupo B:** `app/pipeline.py`, `app/llm/*.py`, y el módulo nuevo que necesites
para reintentos y breaker. Tests: `tests/test_pipeline.py`, `tests/test_llm_deepseek.py`,
y el de tu módulo nuevo.

---

## Grupo C — Prefiltro, prompt y few-shot

### C1. El veto compara por subcadena *(gravedad media)*

**Reproducción:** con `tecnologias_veto=["java"]`, una oferta titulada *"Senior JavaScript
Developer"* se descarta con motivo `palabra vetada: java`. Con vetos cortos (`go`, `c`, `r`)
es peor: `go` casa dentro de `Django`.

**Esperado:** el veto casa por palabra completa, no por subcadena. Contradice el principio
declarado del propio módulo, *"ante la duda, no descarta"*: hoy descarta ofertas válidas sin
que el LLM llegue a verlas.

Ojo con los vetos multipalabra (`business intelligence`) y con los que llevan puntuación
(`c++`, `node.js`, `c#`): `normaliza()` elimina esos caracteres, así que `c++` y `c` colapsan
al mismo token. Decide cómo tratarlo y déjalo documentado en el código.

**Tests requeridos:**
- `java` no descarta *"JavaScript Developer"*.
- `java` sí descarta *"Desarrollador Java senior"*.
- `go` no descarta una oferta que mencione *Django*.
- Un veto de varias palabras sigue funcionando.

### C2. El prompt envía `None` como si fuera un dato *(gravedad media)*

**Reproducción:** con `salario_min=50000` y `salario_max=None`, el prompt contiene
literalmente `Salario: 50000.0 - None`. La regla 1 del propio `PROMPT_SISTEMA` y el spec
prohíben presentar un dato ausente como valor.

**Esperado:** el salario se formatea de manera legible en los cuatro casos: sólo mínimo,
sólo máximo, ambos, ninguno. Lo ausente se dice ausente.

**Tests requeridos:** un test por cada uno de los cuatro casos, comprobando que en ninguno
aparece `None` ni un rango inventado.

### C3. El few-shot no acota el presupuesto de tokens *(hueco del spec)*

**Reproducción:** `ejemplos_few_shot()` limita el número de ejemplos (8) pero no su tamaño,
y `classify.py` inyecta el motivo del usuario íntegro. Un motivo largo infla el prompt sin
límite.

**Esperado:** un presupuesto acotado y explícito, como pide el spec. Truncar por longitud es
suficiente; no hace falta contar tokens de verdad.

**Tests requeridos:**
- Un motivo muy largo se trunca y el bloque de ejemplos no supera el presupuesto.
- Los ejemplos cortos no se tocan.
- El equilibrado entre positivos y negativos sigue funcionando después de truncar.

**Ficheros del grupo C:** `app/prefilter.py`, `app/classify.py`, `app/feedback.py`.
Tests: `tests/test_prefilter.py`, `tests/test_classify.py`, `tests/test_feedback.py`.

---

## Grupo D — Perfil

### D1. Resubir el CV pisa la edición manual *(hueco del spec)*

**Reproducción:** las columnas `Perfil.ruta_pdf` y `Perfil.editado_a_mano` existen en
`app/models.py` pero no se leen en ningún sitio. `cli.py comando_cv` re-extrae siempre e
inserta una fila nueva, y `_carga_perfil()` toma la de `id` más alto. Un CV resubido pisa
cualquier corrección manual previa.

**Esperado, lo que dice el spec:** *"la extracción sólo se repite si se sube un PDF distinto"*
y *"si el modelo entiende algo mal, la corrección manual del usuario prevalece"*.

Necesitas poder saber si el PDF es el mismo. Un hash del contenido es suficiente; requiere
una columna nueva en `Perfil`. Como no hay migraciones en el proyecto (las tablas se crean
con `create_all`), añade la columna y documenta en el README que una base de datos anterior
hay que borrarla — es una herramienta local monousuario y no merece Alembic.

**Tests requeridos:**
- Subir el mismo PDF dos veces no vuelve a llamar a la extracción.
- Subir un PDF distinto sí extrae de nuevo.
- Con `editado_a_mano=True`, resubir el mismo PDF no pisa los datos editados.
- Un PDF distinto con edición manual previa: decide el comportamiento, documéntalo en el
  código y cúbrelo con un test. No lo dejes implícito.

**Ficheros del grupo D:** `app/profile.py`, `app/cli.py`, `app/models.py`, `README.md`.
Tests: `tests/test_profile.py`, `tests/test_cli.py`.

---

## Cierre

Al terminar todos los grupos:

- [ ] `docker compose run --rm app pytest` en verde, con los de contrato deseleccionados.
- [ ] Ningún test borrado o debilitado para poner verde. Si un test viejo deja de tener
      sentido tras un arreglo, se sustituye por otro que cubra lo mismo.
- [ ] El README refleja los cambios de comportamiento visibles para el usuario.
