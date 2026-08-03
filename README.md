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
