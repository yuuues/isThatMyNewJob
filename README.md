# isThatMyNewJob

Clasificador local de ofertas de empleo. Recoge ofertas a diario desde varias APIs de
agregadores, las deduplica, filtra las irrelevantes y clasifica el resto según su encaje
con un CV y unas preferencias, mostrando el resultado en una web local.

No es un clasificador de currículums: es su inverso. Se clasifican ofertas contra un CV.

## Estado

Pipeline e interfaz web funcionando. El run diario (ingesta, deduplicación, prefiltro y
clasificación) se programa dentro del mismo proceso que la web. Ver
[el spec](docs/superpowers/specs/2026-08-03-clasificador-ofertas-design.md).

## Empezar

Hacen falta Docker y las claves de las APIs que se vayan a usar. Cuatro pasos:

```bash
cp .env.example .env          # y rellenar las claves que se vayan a usar
cp seed.example.yaml seed.yaml # preferencias y búsquedas iniciales

docker compose run --rm app python -m app.cli init --semilla seed.yaml
docker compose run --rm app python -m app.cli cv ruta/al/cv.pdf

docker compose up
```

Y abrir <http://localhost:8100>.

`init` crea `data/app.db` y carga preferencias y búsquedas desde el YAML. `cv` extrae el
perfil del PDF (ver [El CV y el perfil](#el-cv-y-el-perfil)). Los dos pasos se pueden
hacer luego desde la web, pero sin perfil no hay clasificación posible, así que conviene
empezar por ahí.

**Si ya tenías una base de datos de antes, bórrala primero:** ver
[Bases de datos anteriores](#bases-de-datos-anteriores).

## El run diario

`docker compose up` levanta la web en el puerto 8000 y, en el mismo proceso, el
planificador del run diario. La hora se configura con `HORA_RUN_DIARIO` (formato `HH:MM`,
zona `Europe/Madrid`, por defecto las 07:00).

El planificador **sólo se enciende con el comando que arranca la web**. Va en el `CMD` del
`Dockerfile` (`SCHEDULER_ACTIVO`) y no en el entorno del contenedor, porque
`docker compose run --rm app pytest` y `docker compose run --rm app python -m app.cli …`
sustituyen ese comando: así ni los tests ni la CLI heredan un planificador programando
runs contra las APIs de verdad. Para dejarlo apagado también en `up`, basta con poner
`SCHEDULER_ACTIVO=0` en el `.env`.

Un run se puede lanzar también a mano desde la vista de búsquedas, con un límite de uno
cada seis horas: el aviso legal de Remotive pide como mucho unas cuatro peticiones al día.

## La web

Cinco vistas, todas en <http://localhost:8100>:

| Vista | Para qué |
|---|---|
| `/` | Las ofertas clasificadas, agrupadas en `aplicar_ya`, `revisar` y `descartar` |
| `/job/{id}` | El detalle de una oferta y el botón de reclasificar |
| `/profile` | El perfil extraído del CV, editable, con su histórico |
| `/preferences` | Salario, modalidades, zonas, vetos, idiomas y notas |
| `/searches` | Las búsquedas guardadas, su coste en créditos y "buscar ahora" |
| `/runs` | Histórico de runs, descartes por regla, errores y cupo de JSearch |

Tres cosas que no se ven a simple vista y conviene saber:

- **Al decidir sobre una oferta, escribe el motivo.** Las decisiones con motivo escrito
  son las que se usan como ejemplos para afinar el clasificador; las que no lo tienen se
  ignoran. Marcar sin explicar no enseña nada.
- **`rechazado por ellos` no es un ejemplo negativo.** Que una empresa descarte al
  candidato no dice nada sobre lo que el candidato quiere, así que ese estado no se usa
  para enseñar al clasificador. `no me interesa` sí.
- **Los descartes por regla se revisan en `/runs`.** Un veto mal puesto oculta ofertas
  válidas en silencio y sin gastar llamada al modelo; ésa es la vista donde se ve y desde
  donde se devuelve una oferta a la cola.

El selector de la barra de navegación cambia entre tema claro y oscuro. De serie va en
`Tema: automático`, que es seguir al sistema operativo; elegir claro u oscuro lo fija para
ese navegador y se recuerda en `localStorage`. No se guarda en la base de datos: es una
preferencia del dispositivo, no del candidato.

## Preferencias

Se configuran en `/preferences` o, la primera vez, con `init --semilla seed.yaml`. Dos
campos merecen atención:

- **Notas.** Texto libre que se inyecta tal cual en el prompt del clasificador. Es lo que
  más lo afina: "prefiero producto sobre consultoría", "nada de guardias". Dejarlo vacío
  desaprovecha la mitad del sistema.
- **Vetos** (sectores y tecnologías). Se aplican antes de llamar al modelo, así que lo
  vetado se descarta sin coste pero también sin criterio: vetar `java` llegó a ocultar 3
  ofertas válidas de 197. Al guardar, la vista ofrece reevaluar el prefiltro sobre lo ya
  descartado por regla, que es como vuelven a la cola las ofertas de un veto retirado.

## Stack

Python 3.12 en un único contenedor Docker, para uso local. FastAPI + Jinja2 + HTMX
(servido en local, sin CDN), SQLite y APScheduler. Gemini y DeepSeek como proveedores de
LLM: ver [Modelos](#modelos).

`app/web/static/htmx.min.js` es HTMX 2.0.10 y `pico.min.css` es Pico CSS 2.1.1, ambos
descargados y versionados en el repositorio a propósito: la herramienta tiene que
funcionar sin red y una dependencia de CDN es un punto de fallo gratuito.

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
  para que no confunda "no lo veo" con "el puesto no lo pide". En la web esas ofertas
  llevan una marca visible.
- **JSearch lleva presupuesto mensual persistido.** Con run diario, cada búsqueda que
  la use cuesta unos 30 créditos al mes: caben 5 o 6. Al agotarse, la fuente se salta
  y las demás siguen funcionando. Configurable con `JSEARCH_LIMITE_MENSUAL`, y el
  consumo del mes en curso se ve en `/runs`.

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

El perfil se extrae del PDF del currículum, desde la web en `/profile` o por línea de
comandos:

```bash
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
  se avisa. Manda el CV nuevo — clasificar ofertas contra una experiencia desactualizada
  es peor que rehacer una corrección —, pero el perfil anterior no se destruye: se
  conserva en el histórico de la tabla `profile`, que se consulta en `/profile`, de donde
  se pueden recuperar las correcciones para volver a aplicarlas sobre la extracción nueva.

El perfil también se puede editar a mano en `/profile`; al guardarlo queda marcado como
editado a mano, que es lo que hace que el sistema avise antes de pisarlo con un CV nuevo.

## Bases de datos anteriores

El proyecto no usa migraciones: las tablas se crean con `create_all`, **y `create_all` no
altera tablas que ya existen**. Una base de datos creada con un esquema anterior no se
actualiza sola: se queda sin las columnas nuevas y la web falla al leerlas.

Se nota nada más entrar: la portada responde 500 y el log dice
`sqlite3.OperationalError: no such column: decision_1.aplicada_en`.

La tabla `decision` ha cambiado desde entonces: sus estados ya no son
`interesa`/`descartada`/`aplicada`, sino `guardada`, `aplicada`, `en_proceso`,
`rechazado_por_ellos` y `descartada_por_mi`, y ha ganado las columnas `aplicada_en` y
`actualizada_en`. Antes, `profile` había ganado `hash_pdf`.

**Si tienes una base de datos creada antes de estos cambios, bórrala** — `data/app.db` — y
vuelve a ejecutar `init` y `cv`. Es una herramienta local monousuario y no compensa
mantener migraciones. Se pierden las decisiones anteriores; las ofertas se vuelven a
recoger en el siguiente run.

## Desarrollo

```bash
docker compose run --rm app pytest -q
```

El comando sustituye el `CMD` de la imagen, así que la suite corre siempre con el
planificador apagado. Ningún test llama a una API real ni a un LLM, ni escribe en
`data/app.db`: los de la web usan SQLite en memoria y dobles de los proveedores.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Los ficheros de `tests/fixtures/` son muestras recortadas de respuestas de las APIs de
Adzuna, JSearch, Remotive y Arbeitnow, y siguen sujetos a las condiciones de uso de cada
proveedor: la licencia MIT cubre el código, no esos datos.
