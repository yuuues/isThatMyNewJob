# Filtro por antigüedad en el listado — Diseño

Fecha: 2026-08-06
Estado: aprobado

## Problema

El listado muestra ofertas publicadas hace más de un año como si fueran actuales. Medido
sobre las 434 ofertas de la base:

| antigüedad | ofertas |
|---|---|
| menos de 1 mes | 303 (70 %) |
| 1-3 meses | 61 (14 %) |
| 3-6 meses | 35 (8 %) |
| 6-12 meses | 19 (4 %) |
| más de 1 año | 14 (3 %) |

**33 ofertas de más de seis meses siguen visibles y sin marcar como cerradas.**

El caso que lo motiva: el usuario encontró tres ofertas de SlashMobility que parecían
duplicadas y estaban cerradas en origen. No eran duplicadas —los títulos son distintos y
una es Middle y otra Senior— sino **viejas**: de junio de 2025, agosto de 2025 y octubre
de 2025. La empresa es una consultora que republica el mismo rol cada pocos meses.

`publicada_en` es fiable: de 434 ofertas sólo 2 no la traen, ambas de JSearch.

## Decisiones y alternativas descartadas

### Filtrar en la web, no excluir en la ingesta ni en el prefiltro

Descartado excluir las ofertas viejas antes de guardarlas o descartarlas por regla.

El motivo es que **el ruido de las viejas no cuesta dinero**. Una oferta se ingiere y se
clasifica una vez; la deduplicación impide que se vuelva a pagar aunque la fuente la
sirva otra vez. Lo único que hacen las viejas es ocupar sitio en la lista. Un problema
presentacional se arregla en la presentación.

Excluirlas tendría sentido si ahorrase llamadas al LLM, y no lo hace.

### El umbral por defecto: 3 meses

Descartados 30 días y 6 meses.

**30 días se descarta por evidencia directa del usuario.** De sus 15 decisiones, 6 son
sobre ofertas de más de 30 días, y tres de ellas son candidaturas enviadas: una a 88 días,
otra a 65 y otra a 35. La de 88 días se envió el mismo día en que se midió esto. Un corte
a 30 días esconde casi la mitad de lo que hoy merece mirarse: 43 de las 99 ofertas en
`aplicar_ya` o `revisar`.

**6 meses era la recomendación por los datos**, porque esconde las 33 claramente rancias
sin tapar ni una oferta sobre la que el usuario haya actuado nunca.

**Se elige 3 meses por decisión del usuario**, con la consecuencia entendida y aceptada:
oculta 23 de las 99 ofertas interesantes, y la candidatura de 88 días sale de la vista dos
días después. Lo que hace la elección defendible es que el desplegable la devuelve con un
clic, así que equivocarse por abajo es barato y reversible.

| umbral | ofertas interesantes ocultas |
|---|---|
| 30 días | 43 de 99 |
| 90 días (elegido) | 23 de 99 |
| 180 días | 9 de 99 |

### Las ofertas sin fecha se muestran siempre

Son 2, ambas de JSearch. No saber cuándo se publicó una oferta no es motivo para
esconderla: es el mismo principio que `aplica_prefiltro()` documenta como "ante la duda,
no descarta".

## Arquitectura

Un parámetro más en `listado()` de `app/web/routes_ofertas.py`, una condición más en la
lista por comprensión que ya filtra por texto, estado y cerradas, y un `<select>` más en
`ofertas.html`, calcado del de "Ocultar las cerradas".

```python
@router.get("/", response_class=HTMLResponse)
def listado(
    ...,
    antiguedad: str = Query(default=ANTIGUEDAD_POR_DEFECTO),
    ...,
)
```

El filtrado del listado ya ocurre en Python sobre la lista de candidatas, así que esto es
una condición más y no una consulta nueva.

```python
# Días de antigüedad que se muestran por defecto. Medido: la candidatura más antigua del
# usuario tenía 88 días, así que este umbral la deja fuera por dos días. Es una elección
# suya y consciente; el desplegable la recupera con un clic.
ANTIGUEDAD_POR_DEFECTO = "90"
OPCIONES_ANTIGUEDAD = [("30", "Del último mes"), ("90", "De los últimos 3 meses"),
                       ("180", "De los últimos 6 meses"), ("todas", "De cualquier fecha")]


def _es_reciente(job: Job, antiguedad: str, ahora: datetime) -> bool:
    """Si la oferta entra en la ventana pedida.

    Sin fecha de publicación se muestra siempre: no saber cuándo se publicó no es motivo
    para esconderla, igual que `aplica_prefiltro()` no descarta ante la duda. Son 2 de 434
    y todas de JSearch.
    """
    if antiguedad == "todas" or job.publicada_en is None:
        return True
    return (ahora - job.publicada_en).days <= int(antiguedad)
```

Un valor no reconocido en el parámetro (`?antiguedad=pepe`) se trata como "todas": el
formulario sólo ofrece valores válidos, así que llegar con otra cosa es una URL escrita a
mano y esconder ofertas por ello sería lo peor que puede hacer.

Sin columnas nuevas, sin migración, sin tocar el prefiltro ni la ingesta.

## Pruebas

En `tests/web/test_ofertas.py`, que es donde ya viven los tests del listado y sus filtros:

- el defecto oculta una oferta publicada hace 200 días
- el defecto muestra una publicada hace 10 días
- con `antiguedad=todas` aparece la de 200 días
- una oferta **sin** `publicada_en` se ve con cualquier umbral, incluido el más estrecho
- el desplegable conserva la opción elegida al recargar
- el filtro **se combina** con los demás en vez de pisarlos: una oferta reciente pero
  cerrada sigue oculta si no se piden las cerradas
- un valor no reconocido no esconde nada

## Fuera de alcance

- Marcar como cerradas las ofertas viejas. Antigüedad no es lo mismo que cierre, y
  `app/cerradas.py:8` ya explica por qué el cierre no se deduce.
- Usar el 404 del scraper de Adzuna para marcar `cerrada`. Es una idea buena y barata
  —hoy se detecta y se tira— pero es otro trabajo con su propio spec.
- Deduplicar las ofertas que una misma empresa republica cada pocos meses. El caso de
  SlashMobility no se arregla deduplicando: son puestos distintos y lo que molesta de
  ellos es la antigüedad.
