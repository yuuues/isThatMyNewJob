# El juicio de zona pasa al clasificador — Diseño

Fecha: 2026-08-06
Estado: aprobado

## Problema

El prefiltro decide si una oferta cae dentro de las zonas del candidato usando el campo
`ubicacion`, que viene del agregador. Ese campo no es de fiar, y falla siempre en la
misma dirección: dejando pasar ofertas que deberían pararse.

Medido sobre las 434 ofertas de la base real:

- **11 ofertas híbridas o presenciales dicen "España"** según el proveedor mientras su
  propio texto nombra Madrid, Sevilla, Alicante, Burgos o Baleares. `prefilter.py:126`
  exime el ámbito nacional de la regla de zona a propósito —"descartar por no saber"
  sería peor—, así que las 11 se cuelan enteras hasta el clasificador.
- **6 ofertas traen una provincia concreta que el texto contradice.** La más ilustrativa
  es la 75: el proveedor la sitúa en Barcelona y el título dice "1 día semana oficina en
  Madrid". Si esa oferta fuera híbrida, la regla la habría **conservado** por estar
  supuestamente en zona.

El caso que lo motiva es el job 87. Su descripción dice literalmente *"Híbrido (presencial
en Alicante)"*; Adzuna la sitúa en "España" y Scrappa en "Para, Asturias provincia", que
además huele a error de parseo. Ninguna de las dos fuentes acierta. El usuario la descartó
a mano tras leerla, escribiendo como motivo "Ubicación híbrida en Alicante": el dato
estaba en la oferta y el sistema no tenía forma de verlo.

**El LLM ya recibe las zonas del candidato** —`_bloque_preferencias()` vuelca las
preferencias enteras salvo `notas`— y la regla 2 del prompt ya le manda descartar cuando
se incumple una preferencia explícita. Tuvo la oportunidad de cazar el job 87 y no la
cazó: leyó `Ubicación: España` en la ficha y no fue a buscar la ciudad en el texto. Nadie
se lo había pedido.

## Decisiones y alternativas descartadas

### Quién juzga la zona: el prefiltro Y el clasificador

La regla de zona de `prefilter.py:124-128` **se queda como está**, y el clasificador
recibe el encargo de juzgar la ubicación real deducida del texto.

Descartadas:

- **Quitar la regla y que juzgue sólo el LLM.** Es lo que parecía obvio al principio.
  Medido: la regla descarta 59 ofertas de 434, y revisando las 6 discrepancias
  proveedor-texto **no aparece ni un descarte equivocado**; todos sus fallos son en la
  otra dirección, dejando pasar. Quitarla costaría $0,54/mes en llamadas para volver a
  deducir algo que ya estaba bien.
- **Conservarla sólo cuando el texto confirme la ubicación.** Exige un extractor local de
  lugares, que se descarta abajo por cobertura.

Límite honesto de lo medido: sólo se pueden verificar las ofertas cuyo texto nombra alguna
provincia, que son un tercio. De los 59 descartes, la mayoría no son comprobables por esta
vía. La afirmación defendible es "no hay evidencia de falsos descartes", no "no los hay".

Consecuencia asumida: la política de zona vive en dos sitios. No entran en conflicto —el
prefiltro descarta un subconjunto y el clasificador decide sobre el resto— pero un cambio
de criterio obliga a tocar los dos.

### La ubicación la deduce el LLM, no un extractor local

Descartado un `detecta_ubicacion(texto)` en `app/sources/comun.py`, hermano del
`detecta_modalidad()` que ya existe.

El motivo es la cobertura, medida con una lista de las 52 provincias y sus principales
alias sobre las 434 ofertas:

| lugares nombrados en el texto | ofertas |
|---|---|
| ninguno | 278 (64 %) |
| uno solo | 141 (32 %) |
| dos o más | 15 (4 %) |

Un extractor por lista acertaría en un tercio de los casos y callaría en el resto. La
ambigüedad, que era la objeción intuitiva, resulta ser el problema menor: sólo el 4 % de
las ofertas nombra más de una provincia.

El LLM ya lee la descripción completa y puede razonar sobre dónde se trabaja aunque la
ciudad no aparezca escrita —por el idioma, por el cliente, por el convenio—. Duplicar ese
trabajo con una lista que hay que mantener no compensa.

### Rehacer las 334 clasificaciones existentes

Se rehacen todas, saltando las que tienen `Decision`.

El motivo principal no es el eje nuevo: es que esas 334 se emitieron con
`deepseek-v4-flash` y el prompt v2. El proveedor pasó a `deepseek-v4-pro` el mismo día,
después de una evaluación a ciegas sobre 7 ofertas donde los dos modelos discrepaban y en
la que **pro coincidió con el criterio del usuario 6 veces y flash 1**. Con pro a
$0,001334 por oferta, rehacer las 334 cuesta **$0,45 una vez**.

Descartadas: dejarlo sólo hacia adelante (mantiene el 100 % de la lista juzgada por el
modelo peor) y rehacer sólo las 11 afectadas por la zona (deja el 97 % igual).

Efecto esperado y aceptado: **muchas ofertas van a cambiar de categoría, y no todas a
mejor**. Pro es sistemáticamente más conservador —en la muestra medida, 8 de cada 10
desacuerdos movían la oferta hacia abajo— así que habrá un trasvase notable de
`aplicar_ya` a `revisar`.

## Arquitectura

### El prompt

Una regla 8 en `PROMPT_SISTEMA` de `app/classify.py`:

> El campo `Ubicación` lo da el agregador y a menudo es genérico ("España") o
> directamente erróneo. Deduce del texto dónde se trabaja de verdad. Si el puesto es
> presencial o híbrido y la ubicación real cae fuera de las zonas del candidato, la
> categoría es `descartar` aunque el campo `Ubicación` diga otra cosa.

`PROMPT_VERSION` pasa de 2 a 3. Es un registro, no un disparador: nada se reclasifica
solo, y `oferta.html` ya muestra con qué versión se juzgó cada oferta.

### El eje

`EjesEncaje` en `app/schemas.py` gana un sexto campo `zona`, y `ETIQUETAS_EJES` en
`app/web/routes_ofertas.py` su etiqueta "Zona".

Existe para que la decisión sea auditable: sin él, un descarte por ubicación es
indistinguible de uno técnico, y el usuario no puede saber si el modelo miró la zona o se
la saltó.

**No hace falta migración ni tolerancia hacia atrás**: `_ejes()` en
`routes_ofertas.py:316` ya está escrito para esto —su docstring dice "más los que traiga
de más una versión futura"— y sólo pinta las claves presentes en el JSON guardado. Las 334
clasificaciones viejas seguirán mostrando sus cinco filas sin el eje nuevo y sin error.

### El rehacer

`app/reclasifica.py`, un módulo pequeño:

```python
def marca_para_reclasificar(sesion: Session, *, saltar_decididas: bool = True) -> int
```

Por cada oferta con clasificación: borra la fila de `Clasificacion`, pone
`estado_clasificacion = "pendiente"`, limpia `motivo_regla` y **deja
`intentos_clasificacion` a cero**. Devuelve cuántas marcó.

Ese último punto no es cosmético y ya mordió una vez hoy: sin él, una oferta que hubiera
agotado los tres intentos vuelve a la cola y el bucle de `pipeline.py:201` la manda al
estado terminal nada más sacarla, sin clasificarla ni una vez. `reintentar()` en
`routes_runs.py:246` documenta la misma trampa.

Se expone como `python -m app.cli reclasificar`. **No clasifica**: marca y sale. El
trabajo lo hace el bucle del run siguiente, que ya sabe hacerlo. Con 334 ofertas y
`max_clasificaciones_por_run = 200`, hacen falta dos runs o subir el tope una vez; el
comando lo dice al terminar.

Las ofertas con `Decision` se saltan por el mismo criterio que en el enriquecimiento: el
usuario ya las juzgó a mano y reopinar no aporta nada.

## Errores

No hay caminos de error nuevos. El clasificador ya trata el fallo del modelo con
reintentos y estado terminal, y `marca_para_reclasificar()` no hace red: sólo escribe en
la base, con un commit al final.

El riesgo real del cambio no es una excepción, es que **la regla 8 no funcione**: que el
modelo siga sin deducir la ubicación del texto. Por eso existe el test de contrato de
abajo, y por eso se ejecuta ANTES de gastar los $0,45 en rehacer las 334.

## Pruebas

**Esquema y prompt** (`tests/test_schemas.py`, `tests/test_classify.py`):

- `EjesEncaje` exige el campo `zona`
- el prompt del sistema incluye la instrucción de deducir la ubicación del texto
- `PROMPT_VERSION` es 3
- una clasificación con eje `zona` se guarda y se recupera entera

**Web** (`tests/web/test_detalle.py`):

- la ficha pinta el eje `zona` con su etiqueta "Zona"
- **una clasificación guardada SIN el eje `zona` sigue pintando sus cinco filas sin
  error** — la regresión que protege a las 334 existentes

**Rehacer** (`tests/test_reclasifica.py`):

- marca las ofertas clasificadas y borra su `Clasificacion`
- deja `intentos_clasificacion` a cero
- salta las ofertas con `Decision` y les conserva la clasificación
- devuelve el número de ofertas marcadas
- una oferta sin clasificación previa no se toca

**Contrato** (`tests/test_classify_contrato.py`, marcado `contrato` y excluido de la
suite): el texto real del job 87 —"Híbrido (presencial en Alicante)" con
`ubicacion = "España"`— contra el LLM real y unas preferencias con `zonas: ["barcelona"]`,
exigiendo `categoria == "descartar"`.

Es el único test que comprueba que el cambio hace lo que promete. Los demás verifican que
el campo existe y se guarda; éste, que el modelo lo usa.

## Fuera de alcance

- Extraer la ubicación a un campo estructurado de `Job`. El eje `zona` es texto para leer,
  no un dato para filtrar.
- Corregir el campo `ubicacion` que dan los proveedores.
- La deduplicación de ofertas repetidas entre fuentes, que es el trabajo siguiente y tiene
  su propio spec. Se decidió hacer este primero porque, con la zona ya juzgada sobre el
  texto, la pregunta de qué copia gana al fusionar deja de decidir si una oferta se filtra
  o no.
- Tocar la regla de zona del prefiltro.
