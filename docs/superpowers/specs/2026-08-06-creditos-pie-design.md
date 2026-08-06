# Créditos en el pie

## Qué se quiere

Que la aplicación diga quién la ha hecho y dónde está el código. Hoy el pie sólo lleva
el aviso de que la herramienta es local, y no hay ninguna forma de llegar al repositorio
ni al autor desde la propia web.

## Qué NO se hace, y por qué

- **Ninguna vista `/about`.** Se valoró y se descartó: los enlaces caben en el pie, que
  ya está en todas las vistas, y una vista más obliga a tocar el `<nav>` —que ya lleva
  cinco entradas más el selector de tema— y a mantener una plantilla nueva.
- **Nada de "cómo está hecho" dentro de la app.** Eso ya vive en la sección `## Stack`
  del README. Duplicarlo garantiza que las dos copias se separen.
- **Ni iconos ni logos.** La regla 1 de `base.html` es que nada se carga de fuera, así
  que un icono de GitHub o de Liberapay habría que descargarlo y versionarlo. No compensa
  para tres enlaces de texto.

## El pie

Dos párrafos dentro del `<footer>` que ya existe: el aviso arriba, los créditos abajo.

```
Herramienta local. Los datos no salen de esta máquina.
Código en GitHub · Hecho por Yuuu · yuuu.es · Liberapay
```

Tres enlaces:

| Texto | Destino |
|---|---|
| `Código en GitHub` | `https://github.com/yuuues/isThatMyNewJob` |
| `yuuu.es` | `https://yuuu.es` |
| `Liberapay` | `https://liberapay.com/YuuuES` |

`Hecho por Yuuu` es texto plano; el enlace es `yuuu.es`.

### Dos párrafos y no uno

Con tres enlaces, la línea única pasa de los cien caracteres y envuelve por donde le
toque en una ventana estrecha, dejando el aviso de privacidad partido a media frase.
Separarlos también evita leer "los datos no salen de esta máquina · Código en GitHub"
como una sola idea.

Son dos `<p><small class="tenue">…</small></p>`, no `<div>`: el HTML de este proyecto es
semántico porque Pico estiliza etiquetas, no clases inventadas. Si el hueco vertical
entre los dos párrafos queda excesivo, se aprieta con una regla en `estilo.css` —que es
donde vive el ajuste de densidad—, nunca metiendo un `<br>` en la plantilla.

### `target="_blank" rel="noopener noreferrer"`

Los tres enlaces lo llevan entero.

- `target="_blank"` porque salir de la herramienta para ver un perfil de GitHub es
  perder el sitio donde estabas en el listado.
- `noreferrer` no es adorno: sin él el navegador manda `http://localhost:8100/job/123`
  como cabecera `Referer` a GitHub, a yuuu.es y a Liberapay. Es exactamente lo que niega
  la frase que hay dos líneas más arriba.
- `noopener` es la higiene habitual de `_blank`, y va de todas formas implícito en
  `noreferrer`; se escribe explícito para que se lea la intención.

## Dos tests existentes que sobran

Hay dos tests que afirman que la cadena `https://` no aparece en `base.html`:
`test_el_selector_de_tema_no_carga_nada_de_fuera` en `tests/web/test_tema.py` y
`test_la_plantilla_base_no_contiene_ninguna_url_externa` en `tests/web/test_arranque.py`.
Con tres enlaces externos en el pie, los dos fallan.

La afirmación era demasiado ancha para lo que quería decir. Lo que hay que garantizar es
que la plantilla no **carga** nada de fuera: un `<script src>`, un `<link href>` o un
`<img>` contra un CDN. Un `<a href>` no carga nada; lo sigue el usuario si quiere, y
seguirlo requiere la red que la herramienta no necesita para funcionar.

Eso ya lo comprueba, y bien, `test_la_pagina_no_carga_nada_de_fuera` en
`tests/web/test_arranque.py`: mira los `src` y `href` de las etiquetas que cargan
recursos sobre la página **renderizada**, y su docstring dice ya que los enlaces a las
ofertas originales son externos y deben serlo. Como `base.html` es la plantilla madre de
todas las vistas, renderizar `/` la incluye entera, pie incluido.

Así que no hace falta ningún test nuevo: se borran los dos que buscan la cadena a pelo y
se añade `iframe` a la lista de etiquetas del que se queda, para no perder cobertura.

## Tests

Fichero nuevo `tests/web/test_pie.py`, siguiendo el patrón de `tests/web/test_tema.py`
(un fichero por rasgo de `base.html`). Dos casos:

1. **El pie de una vista real lleva los tres enlaces**, cada uno con su `href` y con
   `rel="noopener noreferrer"`. El `rel` se comprueba a propósito: es el requisito que
   más fácil se pierde en una edición futura, porque quitarlo no rompe nada visible.
2. **Los parciales de HTMX no arrastran el pie.** Ya hay precedente del mismo control
   para `<nav>` en `tests/web/test_ofertas.py`.

## README

Sección `## Autor` nueva, justo antes de `## Licencia`: quién lo hace, `yuuu.es` y
Liberapay.

No lleva enlace al repositorio: el README ya se sirve desde ese repositorio, así que
sería un enlace a sí mismo.

## Ficheros

| Fichero | Cambio |
|---|---|
| `app/web/templates/base.html` | Segundo párrafo en el `<footer>` con los tres enlaces |
| `app/web/static/estilo.css` | Sólo si el hueco entre los dos párrafos queda mal |
| `tests/web/test_pie.py` | Nuevo |
| `tests/web/test_tema.py` | Se le quita el test de "no carga nada de fuera" |
| `tests/web/test_arranque.py` | Se le quita el duplicado; `iframe` entra en el que se queda |
| `README.md` | Sección `## Autor` |
