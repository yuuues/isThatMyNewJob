# isThatMyNewJob

Clasificador local de ofertas de empleo. Recoge ofertas a diario desde varias APIs de
agregadores, las deduplica, filtra las irrelevantes y clasifica el resto según su encaje
con un CV y unas preferencias, mostrando el resultado en una web local.

No es un clasificador de currículums: es su inverso. Se clasifican ofertas contra un CV.

## Estado

Pipeline funcionando por línea de comandos: ingesta, deduplicación, prefiltro y
clasificación. La interfaz web está sin construir. Ver
[el spec](docs/superpowers/specs/2026-08-03-clasificador-ofertas-design.md).

## Stack

Python 3.12 en un único contenedor Docker, para uso local. SQLite, APScheduler.
Gemini y DeepSeek como proveedores de LLM: ver [Modelos](#modelos).

## Fuentes

Sin scraping. Todas las cifras de abajo están medidas contra las APIs reales,
no tomadas de su documentación.

| Fuente | Cobertura | Descripción | Coste |
|---|---|---|---|
| JSearch | España y resto vía Google for Jobs: agrega LinkedIn, Glassdoor, Tecnoempleo, Jooble | **Completa** (mediana 1994 caracteres) | 200 créditos/mes, límite duro. 1 crédito por búsqueda y run |
| Adzuna | España | **Cortada a 500 caracteres** por la propia API | Gratis, registro |
| Remotive | Remoto internacional | Completa | Gratis, sin clave |
| Arbeitnow | Remoto europeo, sobre todo alemán | Completa | Gratis, sin clave |

Dos consecuencias prácticas:

- **Adzuna sirve para descubrir, no para clasificar a fondo.** Sus ofertas se marcan
  como truncadas y el prompt avisa al modelo de que no está viendo los requisitos,
  para que no confunda "no lo veo" con "el puesto no lo pide".
- **JSearch lleva presupuesto mensual persistido.** Con run diario, cada búsqueda que
  la use cuesta unos 30 créditos al mes: caben 5 o 6. Al agotarse, la fuente se salta
  y las demás siguen funcionando. Configurable con `JSEARCH_LIMITE_MENSUAL`.

## Modelos

Hay dos proveedores de LLM implementados, **Gemini** y **DeepSeek**, detrás de un mismo
protocolo (`app/llm/base.py`): un modelo que devuelve JSON conforme a un esquema Pydantic.
Se elige con `PROVEEDOR_CLASIFICACION`, y añadir un tercero es implementar ese protocolo y
una rama en la factoría.

| Tarea | Modelo por defecto | Por qué |
|---|---|---|
| Clasificar ofertas | `gemini-3.5-flash-lite` (`MODELO_GEMINI`) | Unas 100 llamadas al día. Tiene capa gratuita y cuesta 5x menos en entrada que Flash |
| Clasificar ofertas (alternativa) | `deepseek-v4-flash` (`MODELO_DEEPSEEK`) | Con `PROVEEDOR_CLASIFICACION=deepseek` |
| Extraer el perfil del CV | `gemini-3.6-flash` (`MODELO_PERFIL`) | Una sola llamada, multimodal sobre el PDF. Aquí manda la calidad, no el precio |

Dos cosas que conviene saber:

- **La extracción del CV es siempre Gemini.** Es multimodal sobre el PDF y DeepSeek no lo
  soporta, así que no pasa por el protocolo: vive en `app/profile.py` hablando con Gemini
  directamente. Poner `PROVEEDOR_CLASIFICACION=deepseek` cambia quién clasifica las
  ofertas, pero seguirás necesitando `GEMINI_API_KEY` para el comando `cv`.
- **La cuota agotada no se reintenta.** Cualquier fallo del proveedor se trata como
  transitorio y se reintenta, salvo el rate limit o la cuota, que se traducen a
  `CuotaAgotadaError`: ahí el pipeline corta las llamadas, cierra el run y deja la cola
  para el día siguiente. Reintentar sólo gastaría lo que ya no queda.

## El CV y el perfil

El perfil se extrae del PDF del currículum con `cv`:

```
docker compose run --rm app python -m app.cli cv ruta/al/cv.pdf
```

La extracción cuesta una llamada al modelo, así que **sólo se repite si el PDF es
distinto**. El sistema identifica el CV por el contenido del fichero, no por su ruta:
renombrarlo o moverlo no cuenta como CV nuevo.

- **Mismo PDF:** no se llama al modelo y el perfil guardado queda intacto, incluidas las
  correcciones que hayas hecho a mano. Como no hay extracción, este caso tampoco necesita
  `GEMINI_API_KEY`.
- **PDF distinto:** se vuelve a extraer y el perfil nuevo pasa a ser el vigente.
- **PDF distinto habiendo correcciones manuales previas:** también se vuelve a extraer, y
  el comando lo avisa por pantalla. Manda el CV nuevo — clasificar ofertas contra una
  experiencia desactualizada es peor que rehacer una corrección —, pero el perfil anterior
  no se destruye: se conserva en el histórico de la tabla `profile`, de donde se pueden
  recuperar las correcciones para volver a aplicarlas sobre la extracción nueva.

## Bases de datos anteriores

El proyecto no usa migraciones: las tablas se crean con `create_all`. La tabla `profile`
ha ganado una columna (`hash_pdf`, la huella del PDF). **Si tienes una base de datos creada
antes de este cambio, bórrala** — `data/app.db` — y vuelve a ejecutar `init` y `cv`. Es una
herramienta local monousuario y no compensa mantener migraciones.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Los ficheros de `tests/fixtures/` son muestras recortadas de respuestas de las APIs de
Adzuna, JSearch, Remotive y Arbeitnow, y siguen sujetos a las condiciones de uso de cada
proveedor: la licencia MIT cubre el código, no esos datos.
