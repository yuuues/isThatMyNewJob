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

### Deduplicación

Dos ofertas son la misma cuando coinciden **empresa y título**, normalizados y sin la
forma jurídica (`Acme S.L.` y `ACME SL` son la misma empresa).

La ubicación **no** entra en la clave, aunque el spec la incluía. Medido sobre la base
real, con ella dentro la deduplicación no servía para nada:

- Adzuna republica un mismo anuncio como un listado por provincia, cada uno con su `id`.
  Una sola oferta de PayXpert ocupaba **nueve filas**, con nueve scrapes de la ficha y
  nueve llamadas al modelo.
- Cada fuente escribe la ubicación a su manera: para la misma oferta, adzuna dice
  `Barcelona` y scrappa `08029 Barcelona, Barcelona provincia`; o adzuna dice `España`
  donde scrappa dice `Valencia, Valencia provincia`. Así, la clave que existe justamente
  para reconocer la misma oferta llegada por dos fuentes no casaba casi nunca.

La ubicación no se pierde al fundir: se acumulan todas en la oferta que se conserva, la
ficha las enumera y el prefiltro las mira todas antes de vetar por zona — basta con que
una encaje. Lo que se asume a cambio: dos vacantes distintas de la misma empresa con el
mismo título en ciudades distintas se ven como una.

## La web

Cinco vistas, todas en <http://localhost:8100>:

| Vista | Para qué |
|---|---|
| `/` | Las ofertas clasificadas, agrupadas en `aplicar_ya`, `revisar` y `descartar` |
| `/job/{id}` | El detalle de una oferta y el botón de reclasificar |
| `/profile` | El perfil extraído del CV, editable, con su histórico |
| `/preferences` | Salario, modalidades, zonas, vetos, idiomas y notas |
| `/searches` | Las búsquedas guardadas, su coste en créditos de Scrappa y de JSearch, y "buscar ahora" |
| `/runs` | Histórico de runs, descartes por regla, errores y cupo consumido de Scrappa y de JSearch |

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

Cinco fuentes, todas por API. Ninguna cifra viene de la documentación de los proveedores:
las medianas están medidas sobre las 484 ofertas que hay ahora en la base — 189 de
Scrappa, 146 de Adzuna, 118 de Arbeitnow y 21 de Remotive —, salvo la de JSearch, que
conserva su medición original contra la API porque en la base sólo hay diez ofertas suyas.

| Fuente | Cobertura | Descripción | Coste |
|---|---|---|---|
| Scrappa | Indeed: España y otros siete países | **Completa** (mediana 4540 caracteres) | 500 créditos/mes gratis. 1 crédito por **llamada**, con hasta 100 ofertas en cada una |
| JSearch | España y resto vía Google for Jobs: agrega LinkedIn, Glassdoor, Tecnoempleo, Jooble | **Completa** (mediana 1994 caracteres) | 200 créditos/mes, límite duro. 1 crédito por búsqueda y run |
| Adzuna | España | **Cortada a 500 caracteres** por la propia API; se completa leyendo la ficha pública (mediana 3115 tras enriquecer) | Gratis, registro |
| Remotive | Remoto internacional | Completa (mediana 7787 caracteres) | Gratis, sin clave |
| Arbeitnow | Remoto europeo, sobre todo alemán | Completa (mediana 4748 caracteres) | Gratis, sin clave |

Cuatro consecuencias prácticas:

- **Scrappa es la fuente a la que dar prioridad para España.** El crédito se paga por
  llamada y no por oferta, y `limit` llega a 100: con los 500 créditos gratis salen hasta
  50.000 ofertas al mes, varios órdenes de magnitud por encima del resto. Trae además dos
  señales que las demás obligan a adivinar, `is_remote` como booleano y `attributes` con
  la modalidad ya etiquetada. Se piden 50 por llamada (`SCRAPPA_RESULTADOS`, máximo 100)
  para no inflar de golpe la cola del clasificador.
- **Dos fuentes llevan cupo mensual persistido**, Scrappa y JSearch. Al agotarse, la
  fuente se salta y las demás siguen funcionando. Se configuran con
  `SCRAPPA_LIMITE_MENSUAL` (450 por defecto, de 500) y `JSEARCH_LIMITE_MENSUAL` (180, de
  200), dejando margen para diagnóstico. Lo que cada búsqueda activa compromete al mes se
  ve en `/searches`, y lo consumido de verdad en `/runs`: en las dos vistas salen las dos
  fuentes, contadas por separado. Con run diario, cada búsqueda que use JSearch cuesta unos 30 créditos al mes:
  caben 5 o 6, así que conviene reservarla para las que de verdad importan.
- **Adzuna sirve para descubrir; para clasificar a fondo hay que enriquecerla.** Antes de
  prefiltrar, el run lee la ficha pública de cada oferta truncada y sustituye el extracto
  de 500 caracteres por el texto completo (`app/sources/adzuna_web.py`, con tope de
  `ADZUNA_SCRAPE_MAX_POR_RUN` fichas por run —40 por defecto— y un contador de fallos para
  no reintentar eternamente una ficha que Adzuna ya borró; se apaga con
  `ADZUNA_SCRAPE_ACTIVO=0`). Es la única parte del proyecto que lee HTML, y va contra
  `/details/`, que el `robots.txt` de Adzuna no prohíbe.
- **Lo que siga llegando truncado se marca como tal.** El prompt avisa al modelo de que
  no está viendo los requisitos, para que no confunda "no lo veo" con "el puesto no lo
  pide", y en la web esas ofertas llevan una marca visible.

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

### Ofertas repetidas de antes del cambio de deduplicación

La clave dejó de mirar la ubicación, pero las ofertas ya guardadas llevan la clave
anterior: siguen repetidas una vez por ciudad, y las que no tienen duplicado volverían a
entrar como nuevas en el siguiente run. Se arregla una sola vez y sin gastar API:

```bash
docker compose run --rm app python -m app.cli fusionar-duplicados
```

De cada grupo se conserva la fila que más información lleva encima — primero la que tiene
una decisión tuya, luego la que tiene veredicto del modelo — y se le acumulan las
ubicaciones de las demás. No hay que reclasificar nada.

## Desarrollo

```bash
docker compose run --rm app pytest -q
```

El comando sustituye el `CMD` de la imagen, así que la suite corre siempre con el
planificador apagado. Ningún test llama a una API real ni a un LLM, ni escribe en
`data/app.db`: los de la web usan SQLite en memoria y dobles de los proveedores.

## Autor

Hecho por Yuuu — <https://yuuu.es>.

Si te sirve de algo, se agradece un café: <https://liberapay.com/YuuuES>.

## Licencia

MIT. Ver [LICENSE](LICENSE).

Los ficheros de `tests/fixtures/` son muestras recortadas de respuestas de las APIs de
Scrappa, Adzuna, JSearch, Remotive y Arbeitnow, más dos fichas públicas de Adzuna, y
siguen sujetos a las condiciones de uso de cada proveedor: la licencia MIT cubre el
código, no esos datos.
