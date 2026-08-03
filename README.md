# isThatMyNewJob

Clasificador local de ofertas de empleo. Recoge ofertas a diario desde varias APIs de
agregadores, las deduplica, filtra las irrelevantes y clasifica el resto según su encaje
con un CV y unas preferencias, mostrando el resultado en una web local.

No es un clasificador de currículums: es su inverso. Se clasifican ofertas contra un CV.

## Estado

En diseño. Ver [el spec](docs/superpowers/specs/2026-08-03-clasificador-ofertas-design.md).

## Stack previsto

Python 3.12 en un único contenedor Docker, para uso local. FastAPI + Jinja2 + HTMX,
SQLite, APScheduler. Gemini para la extracción del perfil desde el PDF y para clasificar;
DeepSeek disponible como proveedor alternativo.

## Fuentes

Adzuna (España), Remotive y Arbeitnow (remoto internacional). Sin scraping.

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
