# Clasificador de ofertas de empleo — Diseño

Fecha: 2026-08-03
Estado: aprobado

## Problema

Revisar ofertas de empleo a mano es lento y repetitivo. El objetivo es un sistema local
que recoja ofertas de varias fuentes a diario, descarte las irrelevantes y clasifique el
resto según su encaje con un perfil y unas preferencias concretas, mostrando el resultado
en una web local donde revisar y decidir.

Conviene nombrarlo bien: no es un clasificador de currículums, es su inverso. Se clasifican
ofertas contra un CV. La distinción importa porque el CV describe *lo que el candidato es*,
no *lo que quiere*; sin capturar lo segundo por separado, el sistema puntuaría alto ofertas
que el usuario rechazaría al leerlas.

## Decisiones y alternativas descartadas

### Obtención de ofertas: APIs de agregadores

Se descarta el planteamiento inicial de scrapear LinkedIn con sesión autenticada. Dos razones:

- Viola los términos de servicio de LinkedIn de forma explícita.
- El vector de detección más fiable de LinkedIn es la cuenta autenticada. El resultado
  probable es la restricción o el baneo permanente del perfil personal — precisamente
  la cuenta que se necesita para buscar trabajo. El scraping anónimo expone una IP;
  el scraping con sesión expone la identidad.

También se corrige la premisa de partida: LinkedIn expone un endpoint de invitado sin
login, mientras que Indeed está tras Cloudflare con detección de bots agresiva y cerró su
API pública de búsqueda. En la práctica Indeed es el difícil de los dos, no al revés.

La vía elegida son APIs legales de agregadores. LinkedIn e Indeed siguen apareciendo
indirectamente porque Google Jobs los indexa y hay agregadores que lo consultan.

### Fuentes de la v1

| Fuente | Autenticación | Cobertura | Motivo |
|---|---|---|---|
| Adzuna (endpoint España) | `app_id` + `app_key`, registro gratuito | España, agrega varios portales | Mejor relación coste/cobertura para el mercado principal |
| Remotive | ninguna | Remoto internacional | Gratis, sin key, integración trivial |
| Arbeitnow | ninguna | Remoto internacional / Europa | Ídem |

Adzuna sola dejaba sin cubrir el objetivo de "España + remoto internacional", porque
funciona por endpoints de país. Remotive y Arbeitnow tapan ese hueco a coste de integración
casi nulo. Como efecto secundario, tener tres fuentes desde el principio valida que la
interfaz `JobSource` sirve para fuentes heterogéneas, que es donde suele fallar un diseño así.

Pendiente de verificar antes de implementar: forma exacta de los endpoints y esquemas de
respuesta de las tres APIs, y si InfoJobs mantiene API accesible (sería una cuarta fuente
relevante para España).

### Modelos: Gemini único, proveedor intercambiable

Se evaluó una arquitectura de dos etapas (DeepSeek criba, Gemini razona sobre los
supervivientes). Se descarta por relación coste/complejidad:

- Con Adzuna + las dos fuentes remotas y 3-5 búsquedas guardadas, tras deduplicar entran
  del orden de 30-100 ofertas nuevas al día. El coste de clasificarlas con Gemini Flash es
  de unos pocos euros al mes.
- El ahorro real es menor de lo que parece: a la etapa de criba se le envía igualmente el
  texto completo de la oferta, así que sólo se ahorran las llamadas al modelo caro de las
  ofertas descartadas en criba — aproximadamente la mitad del gasto.
- A cambio: doble pipeline, segundo punto de fallo y un segundo prompt que calibrar.

Se conserva el beneficio sin el coste mediante la abstracción `LLMProvider`, con
implementaciones de Gemini y DeepSeek intercambiables por configuración. Si el volumen
crece lo suficiente, la etapa de criba se puede añadir después sin rediseñar.

Reparto: **Gemini** extrae el perfil del PDF (DeepSeek no tiene visión ni ingesta de PDF, así
que aquí no hay alternativa) y clasifica ofertas por defecto. **DeepSeek** queda disponible
como proveedor de clasificación seleccionable por configuración.

### Salida de la clasificación: categoría, no puntuación numérica

Se descarta un score 0-100. Los LLM son notablemente más consistentes clasificando en
categorías discretas que emitiendo números: la diferencia entre un 73 y un 68 suele ser
ruido, no señal. Se usan tres categorías más un nivel de confianza que permite ordenar
dentro de cada una.

### Aprendizaje: few-shot desde las decisiones del usuario

Las decisiones del usuario (interesa / descartada / aplicada, con motivo escrito) se
inyectan como ejemplos en el prompt de clasificación. Se descartan embeddings y fine-tuning:
a esta escala no aportan sobre few-shot y añaden infraestructura considerable. El
mecanismo elegido tiene además la ventaja de ser auditable — el usuario puede leer
literalmente lo que se está enviando al modelo.

### Stack

Python 3.12 en un único contenedor Docker, ejecución local exclusivamente.

- **FastAPI** + **Jinja2** + **HTMX** — la interfaz es una tabla con filtros y botones; una SPA sería peso muerto.
- **SQLite** en volumen (`./data/app.db`). Monousuario y local; se descartó Postgres por no aportar nada a cambio de un contenedor más.
- **APScheduler** in-process para el run diario. Se descartan Celery y Redis por el mismo motivo.
- **httpx** para HTTP, **pydantic** para todos los esquemas, **google-genai** como SDK de Gemini.

Se consideró PHP por familiaridad del usuario y se descartó: Python tiene SDK oficial de
Gemini, mejor soporte documentado para la ingesta multimodal del PDF, y la web requerida es
lo bastante simple como para que el framework no sea el factor decisivo.

## Arquitectura

```
app/
  config.py       settings desde .env
  db.py           engine + sesiones
  models.py       tablas
  sources/
    base.py       interfaz JobSource
    adzuna.py
    remotive.py
    arbeitnow.py
  llm/
    base.py       interfaz LLMProvider
    gemini.py
    deepseek.py
  ingest.py       orquesta fuentes -> normaliza -> dedup -> persiste
  dedup.py        clave canónica
  prefilter.py    reglas deterministas
  profile.py      PDF -> perfil estructurado
  classify.py     construye prompt, llama al LLM, guarda veredicto
  feedback.py     selecciona ejemplos few-shot de decisiones previas
  scheduler.py    run periódico
  web/            rutas + plantillas
```

Las dos fronteras que sostienen el diseño:

**`JobSource`** — `search(query: SearchQuery) -> list[RawJob]`. Cada fuente traduce su
respuesta al esquema común. Añadir InfoJobs es escribir un fichero nuevo.

**`LLMProvider`** — `complete_json(prompt: str, schema: dict) -> dict`. Cambiar de modelo
o de proveedor no toca el pipeline.

Ambas tienen implementación fake, lo que permite probar el pipeline completo sin red ni gasto.

## Modelo de datos

| Tabla | Contenido |
|---|---|
| `profile` | ruta del PDF, perfil JSON extraído, marca de edición manual, fecha |
| `preferences` | salario mínimo, modalidades aceptadas, zonas, sectores veto, tecnologías veto, idiomas, jornada, notas libres |
| `saved_search` | nombre, query, fuentes activas, parámetros, activa |
| `job` | fuente, `external_id`, url, título, empresa, ubicación, modalidad, salario min/max, descripción, fecha de publicación, fecha de ingesta, `hash_dedup`, estado de clasificación |
| `classification` | `job_id`, categoría, confianza, razonamiento, ejes, skills faltantes, red flags, modelo usado, `prompt_version`, fecha |
| `decision` | `job_id`, estado, motivo del usuario, fecha |
| `run` | inicio, fin, estadísticas por fuente, errores |

`prompt_version` en cada clasificación permite reclasificar sólo lo afectado cuando se
afine el prompt, en lugar de todo el histórico.

`decision` es la fuente del few-shot y por tanto del aprendizaje del sistema.

## Flujo del run diario

1. El scheduler abre un `run`.
2. Por cada búsqueda guardada activa y cada fuente asociada, se llama a `search()`.
3. Los resultados se normalizan al esquema común.
4. **Deduplicación** por `(fuente, external_id)` y, además, por hash de
   `empresa + título + ubicación` normalizados. La segunda clave es necesaria porque la
   misma oferta llega por Adzuna y por Remotive con identificadores distintos.
5. **Prefiltro determinista**: reglas veto (zona imposible, idioma, palabras vetadas).
   Lo filtrado se marca `descartada_por_regla` y no consume una llamada al LLM. El prefiltro
   no existe sólo por coste: la deduplicación es obligatoria para no reclasificar lo mismo
   cada día, y las reglas veto eliminan ruido.
6. **Clasificación** de lo que sobrevive.
7. Persistencia del veredicto y cierre del `run` con estadísticas y errores.

Sólo se clasifica lo nuevo. Una oferta ya vista no vuelve a costar una llamada salvo
reclasificación explícita.

## Clasificación

### Extracción del perfil

Al subir el PDF, Gemini lo recibe directamente (ingesta multimodal, sin librería de parseo
intermedia) y devuelve JSON con schema forzado: años de experiencia, roles desempeñados,
skills con nivel, sectores, idiomas, formación, certificaciones, ubicación, disponibilidad.

El resultado se guarda como JSON editable. Si el modelo entiende algo mal, la corrección
manual del usuario prevalece. La extracción sólo se repite si se sube un PDF distinto.

### Prompt de clasificación

Cuatro bloques: perfil, preferencias, ejemplos de decisiones previas, oferta a evaluar.

Salida con schema forzado:

```
categoria         aplicar_ya | revisar | descartar
confianza         alta | media | baja
razonamiento      2-3 frases
ejes              tecnico, seniority, modalidad, salario, sector
skills_faltantes  lista
red_flags         lista
```

Tres reglas duras, las tres dirigidas a evitar que el modelo invente:

- Un dato ausente en la oferta (típicamente el salario) se reporta como *"no publicado"*.
  Nunca se estima.
- Si la oferta viola un veto explícito del usuario, `descartar` es obligatorio por mucho que
  el encaje técnico sea bueno.
- Sin datos suficientes para decidir, `revisar` con confianza baja. No se adivina.

Temperatura 0.2: la consistencia importa más que la variedad.

### Few-shot

`feedback.py` selecciona las 6-8 decisiones más recientes **que tengan motivo escrito** —
una decisión sin justificación no enseña nada — balanceadas entre "interesa" y "descartada",
con un presupuesto de tokens acotado.

## Interfaz web

En español.

| Ruta | Función |
|---|---|
| `/` | Tablero agrupado por categoría, `aplicar_ya` primero, ordenado por confianza y fecha. Filtros por fuente, estado, búsqueda y fecha. Acciones interesa / descartar / aplicada con campo de motivo, inline vía HTMX |
| `/job/{id}` | Oferta completa, razonamiento, ejes, red flags, enlace al original, botón de reclasificar |
| `/profile` | Subir PDF, revisar y editar el perfil extraído |
| `/preferences` | Formulario de preferencias, incluido el campo de notas libres que se inyecta tal cual en el prompt |
| `/searches` | CRUD de búsquedas guardadas y botón "buscar ahora" |
| `/runs` | Histórico de ejecuciones: entradas por fuente, descartes por regla, errores |

El campo de notas libres en preferencias es deliberado: es la válvula de escape para
criterios que no caben en un formulario estructurado.

## Errores

Ningún fallo parcial tumba el run.

| Fallo | Comportamiento |
|---|---|
| Fuente caída o con error | Se registra en el `run`; las demás fuentes continúan |
| LLM con error, timeout o JSON inválido | 2 reintentos con backoff exponencial; después la oferta queda `pendiente_clasificacion` y entra en el run siguiente |
| Cuota del LLM agotada | Circuit breaker: se cortan las llamadas, se cierra el `run` y la cola queda para el día siguiente |
| PDF ilegible | Error visible en `/profile`; no afecta al resto del sistema |

Se aplica rate limiting propio hacia las APIs externas para no agotar cuotas por ráfagas.

Los secretos viven en `.env`, fuera de git y fuera de la base de datos.

## Pruebas

- **Unit**: deduplicación, prefiltro y el normalizador de cada fuente, con respuestas reales
  grabadas como fixtures.
- **Integración**: el run completo contra SQLite en memoria, usando los fakes de `JobSource`
  y `LLMProvider`. Sin red y sin gasto.
- **Contrato**: un test por fuente real contra la API en producción, marcado para ejecución
  manual. No forma parte de la suite habitual.

## Fuera de alcance

Deliberadamente excluido de este diseño: aplicar automáticamente a ofertas, generar cartas
de presentación, soporte multiusuario, notificaciones push y cualquier forma de scraping.
Cada una es un proyecto aparte y ninguna es necesaria para que el sistema cumpla su función.
