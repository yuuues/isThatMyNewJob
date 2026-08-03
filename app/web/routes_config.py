"""Perfil, preferencias y búsquedas guardadas.

Las tres vistas de configuración. El router se monta SIN prefijo: declara la ruta
completa (`/profile`, `/preferences`, `/searches`), según la tabla de rutas del spec.

Este módulo no reimplementa dominio. El CV pasa por `sincroniza_perfil()`, las
reglas por `aplica_prefiltro()` y el run por `ejecuta_run()`; aquí sólo se traducen
formularios HTML a esos esquemas y se decide qué se le enseña al usuario.

Tres cosas que parecen detalles y no lo son:

1. **La extracción del CV entra por dependencia** (`get_extractor_perfil`). Ningún
   test puede llamar a Gemini, y además la dependencia devuelve un invocable
   perezoso: resolverla no construye cliente ni pide credenciales, así que volver a
   subir un CV que no ha cambiado funciona sin `GEMINI_API_KEY`.
2. **Editar una búsqueda actualiza la fila existente.** `carga_semilla()` es
   idempotente por nombre y deja lo que ya había tal cual; ése es un defecto
   conocido del arranque por YAML que la web no puede heredar, porque aquí editar
   es la única forma de corregir una búsqueda.
3. **"Buscar ahora" no ejecuta el run dentro de la petición.** Un run tarda minutos:
   se lanza en un hilo y la vista responde enseguida. Y va limitado, porque el aviso
   legal de Remotive pide un máximo aproximado de cuatro peticiones diarias.
"""

import re
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusquedaGuardada, Job, Perfil, PreferenciasRow, Run, ahora

# Se importa el traductor privado del pipeline en vez de reescribirlo: si la web
# construyera su propio RawJob, el prefiltro de "reevaluar" acabaría juzgando sobre
# campos distintos que el del run y los dos darían resultados diferentes sobre la
# misma oferta, que es justo el fallo que nadie vería.
from app.pipeline import _a_rawjob as job_a_rawjob
from app.prefilter import aplica_prefiltro
from app.profile import (
    PDF_NUEVO,
    PDF_NUEVO_CON_EDICION_PREVIA,
    PRIMERA_EXTRACCION,
    SIN_CAMBIOS,
    perfil_vigente,
    sincroniza_perfil,
)
from app.schemas import PerfilCandidato, Preferencias, SkillPerfil
from app.web.deps import get_plantillas, get_sesion

router = APIRouter()

# Vocabulario de los formularios. Se declara aquí y se pinta desde las plantillas
# para que añadir una fuente o un idioma sea una línea y no una cacería por el HTML.
FUENTES_DISPONIBLES: tuple[str, ...] = ("adzuna", "remotive", "arbeitnow", "jsearch")
MODALIDADES: tuple[str, ...] = ("remoto", "hibrido", "presencial")
# Los tres idiomas que `detecta_idioma()` sabe distinguir. Ofrecer más sería mentir:
# el prefiltro no los reconocería y nunca descartaría por ellos.
IDIOMAS: tuple[str, ...] = ("es", "en", "de")

# Estados de `job.estado_clasificacion` que el prefiltro puede volver a mirar. Sólo
# ése: `clasificada` ya costó una llamada al modelo y `error` es terminal por haber
# agotado los intentos, así que reevaluar ninguno de los dos tendría sentido.
ESTADO_DESCARTADA_POR_REGLA = "descartada_por_regla"
ESTADO_PENDIENTE = "pendiente"

# El run diario es uno al día; el mes de referencia para estimar coste son 30.
RUNS_AL_MES = 30

# Un run manual cada seis horas: cuatro al día como mucho, que es el máximo que pide
# el aviso legal de Remotive. Una sola regla cubre las dos cosas que hay que evitar
# — la ráfaga de dobles clics y el exceso diario — sin llevar dos contadores que
# puedan contradecirse.
INTERVALO_MINIMO_ENTRE_RUNS = timedelta(hours=6)

# Un run sin `fin` más antiguo que esto se da por muerto. Sin esta ventana, un
# proceso que se cayó a mitad de run dejaría el botón inutilizado para siempre.
VENTANA_RUN_EN_CURSO = INTERVALO_MINIMO_ENTRE_RUNS


# '45.000' y '1.234.567': puntos que separan grupos de tres, no decimales.
_MILLARES = re.compile(r"^\d{1,3}(\.\d{3})+$")


class ValorNoValido(ValueError):
    """Un campo del formulario no se puede convertir. Se enseña, no se traga."""


def _ahora_naive() -> datetime:
    """UTC sin zona horaria.

    SQLite devuelve las fechas sin zona, así que comparar contra `run.inicio` con un
    datetime consciente reventaría con un TypeError. Se compara todo sin zona.
    """
    return datetime.now(UTC).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Traducción entre formularios HTML y esquemas
# --------------------------------------------------------------------------


def _lista(texto: str) -> list[str]:
    """Campo de texto separado por comas a lista, sin huecos ni espacios sueltos.

    Se elige la coma y no una línea por elemento porque los vetos son de una o dos
    palabras ('business intelligence') y caben de sobra en una línea.
    """
    return [parte.strip() for parte in (texto or "").split(",") if parte.strip()]


def _numero(texto: str, campo: str) -> float | None:
    """Texto a número, o None si viene vacío. Un valor imposible se rechaza.

    Se acepta la notación española del salario ('45.000', '45.000,50') porque es la
    que teclea cualquiera: si la coma está presente manda como decimal y los puntos
    son millares; si no, sólo se quitan los puntos cuando separan grupos de tres.
    Así '8.5' sigue siendo ocho y medio y no ochenta y cinco.
    """
    limpio = (texto or "").strip().replace(" ", "").replace("€", "")
    if not limpio:
        return None
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif _MILLARES.match(limpio):
        limpio = limpio.replace(".", "")
    try:
        return float(limpio)
    except ValueError:
        raise ValorNoValido(f"{campo} tiene que ser un número: {texto!r}") from None


def _solo_conocidos(valores: list[str], permitidos: tuple[str, ...]) -> list[str]:
    """Filtra lo que no está en el vocabulario, conservando el orden del vocabulario.

    Un formulario es entrada del exterior: si llega una modalidad inventada, pydantic
    reventaría con un 500. Se ignora en vez de romper.
    """
    marcados = set(valores)
    return [v for v in permitidos if v in marcados]


def _skills_desde_texto(texto: str) -> list[SkillPerfil]:
    """Una skill por línea, en 'nombre | nivel | años'.

    Formato de texto y no una tabla de campos numerados porque el perfil real tiene
    del orden de quince skills y editarlas en un <textarea> es más rápido que en
    quince filas de inputs. El nivel y los años son opcionales.
    """
    skills: list[SkillPerfil] = []
    for linea in (texto or "").splitlines():
        if not linea.strip():
            continue
        partes = [p.strip() for p in linea.split("|")]
        nombre = partes[0]
        if not nombre:
            continue
        nivel = partes[1] if len(partes) > 1 else ""
        anios = _numero(partes[2], f"Los años de la skill {nombre!r}") if len(partes) > 2 else None
        skills.append(SkillPerfil(nombre=nombre, nivel=nivel, anios=anios))
    return skills


def _skills_a_texto(skills: list[SkillPerfil]) -> str:
    return "\n".join(
        " | ".join([s.nombre, s.nivel, "" if s.anios is None else _sin_cola(s.anios)]).rstrip(" |")
        for s in skills
    )


def _sin_cola(numero: float) -> str:
    """8.0 se escribe '8'. Un '.0' en un formulario sólo genera dudas."""
    return str(int(numero)) if float(numero).is_integer() else str(numero)


def _formulario_perfil(perfil: PerfilCandidato | None) -> dict[str, str]:
    """Valores del perfil listos para rellenar el formulario de edición."""
    if perfil is None:
        perfil = PerfilCandidato()
    return {
        "anios_experiencia": (
            "" if perfil.anios_experiencia is None else _sin_cola(perfil.anios_experiencia)
        ),
        "titulo_actual": perfil.titulo_actual or "",
        "ubicacion": perfil.ubicacion or "",
        "resumen": perfil.resumen or "",
        "roles": ", ".join(perfil.roles),
        "sectores": ", ".join(perfil.sectores),
        "idiomas": ", ".join(perfil.idiomas),
        "formacion": ", ".join(perfil.formacion),
        "certificaciones": ", ".join(perfil.certificaciones),
        "skills": _skills_a_texto(perfil.skills),
    }


def _perfil_de(fila: Perfil | None) -> PerfilCandidato | None:
    if fila is None or not fila.datos:
        return None
    return PerfilCandidato.model_validate(fila.datos)


def _historico(sesion: Session) -> list[Perfil]:
    """Perfiles anteriores, del más reciente al más viejo, sin el vigente.

    Es donde se recuperan las correcciones manuales cuando se sube un CV nuevo: la
    re-extracción no las arrastra, y sin este listado se perderían.
    """
    filas = sesion.scalars(select(Perfil).order_by(Perfil.id.desc())).all()
    return list(filas[1:])


# --------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------


def get_extractor_perfil() -> Callable[[bytes], PerfilCandidato]:
    """Cómo se convierte un PDF en perfil. Sustituible en tests.

    Devuelve la función, no su resultado: resolver la dependencia no debe construir
    el cliente de Gemini ni exigir credenciales, porque la mayoría de las subidas
    son el mismo CV de siempre y no llegan a extraer nada.
    """
    return _extrae_con_gemini


def _extrae_con_gemini(pdf: bytes) -> PerfilCandidato:
    # Importes dentro de la función: el SDK sólo hace falta cuando se extrae de verdad.
    from app.config import get_settings
    from app.profile import crear_cliente, extrae_perfil

    settings = get_settings()
    return extrae_perfil(
        pdf, cliente=crear_cliente(settings.gemini_api_key), modelo=settings.modelo_perfil
    )


AVISOS_PERFIL = {
    SIN_CAMBIOS: (
        "El CV no ha cambiado, así que no se ha vuelto a llamar al modelo: se conserva "
        "el perfil guardado tal cual, con tus correcciones incluidas."
    ),
    PRIMERA_EXTRACCION: "Perfil extraído del CV.",
    PDF_NUEVO: (
        "CV distinto: se ha vuelto a extraer el perfil. El anterior queda en el histórico."
    ),
    PDF_NUEVO_CON_EDICION_PREVIA: (
        "Aviso: el perfil anterior tenía correcciones manuales y las correcciones NO se "
        "arrastran a la extracción nueva. El perfil anterior sigue en el histórico, aquí "
        "abajo: vuelve a aplicarlas sobre el perfil nuevo."
    ),
}


def _pagina_perfil(
    request: Request,
    sesion: Session,
    *,
    aviso: str | None = None,
    error: str | None = None,
    formulario: dict[str, str] | None = None,
    codigo: int = status.HTTP_200_OK,
) -> HTMLResponse:
    fila = perfil_vigente(sesion)
    perfil = _perfil_de(fila)
    return get_plantillas().TemplateResponse(
        request,
        "perfil.html",
        {
            "titulo": "Perfil",
            "fila": fila,
            "perfil": perfil,
            "formulario": formulario or _formulario_perfil(perfil),
            "historico": _historico(sesion),
            "aviso": aviso,
            "error": error,
        },
        status_code=codigo,
    )


@router.get("/profile", response_class=HTMLResponse)
def ver_perfil(request: Request, sesion: Session = Depends(get_sesion)) -> HTMLResponse:
    return _pagina_perfil(request, sesion)


@router.post("/profile/pdf", response_class=HTMLResponse)
async def subir_cv(
    request: Request,
    pdf: UploadFile = File(...),
    sesion: Session = Depends(get_sesion),
    extractor: Callable[[bytes], PerfilCandidato] = Depends(get_extractor_perfil),
) -> HTMLResponse:
    """Sube el CV y deja el perfil al día. La lógica es de `sincroniza_perfil()`."""
    contenido = await pdf.read()

    # Se mira la cabecera del fichero y no el nombre ni el content-type: los dos los
    # elige el cliente, y un .txt renombrado a .pdf costaría una llamada al modelo
    # para acabar en un error mucho más confuso que éste.
    if not contenido.startswith(b"%PDF-"):
        return _pagina_perfil(
            request,
            sesion,
            error=(
                "El fichero recibido no es un PDF: no empieza por la cabecera '%PDF-'. "
                "Sube el currículum en PDF."
            ),
            codigo=status.HTTP_400_BAD_REQUEST,
        )

    try:
        resultado = sincroniza_perfil(
            sesion,
            contenido,
            # No se guarda el PDF en disco: la ruta es informativa, así que se anota el
            # nombre con el que se subió, que es lo que permite reconocerlo en el histórico.
            ruta=pdf.filename or "cv.pdf",
            extractor=lambda: extractor(contenido),
        )
    except Exception as e:  # noqa: BLE001 - el error del modelo se enseña, no se traga
        sesion.rollback()
        return _pagina_perfil(request, sesion, error=str(e), codigo=status.HTTP_400_BAD_REQUEST)

    return _pagina_perfil(request, sesion, aviso=AVISOS_PERFIL.get(resultado.motivo))


@router.post("/profile", response_class=HTMLResponse)
def guardar_perfil(
    request: Request,
    sesion: Session = Depends(get_sesion),
    anios_experiencia: str = Form(""),
    titulo_actual: str = Form(""),
    ubicacion: str = Form(""),
    resumen: str = Form(""),
    roles: str = Form(""),
    sectores: str = Form(""),
    idiomas: str = Form(""),
    formacion: str = Form(""),
    certificaciones: str = Form(""),
    skills: str = Form(""),
) -> HTMLResponse:
    """Guarda el perfil corregido a mano sobre la fila vigente.

    Se ACTUALIZA la fila vigente en lugar de insertar una nueva, y eso es deliberado:
    la fila conserva `hash_pdf`, así que volver a subir el mismo CV sigue sin gastar
    una llamada al modelo. Si cada corrección creara una fila sin huella, arreglar una
    errata obligaría a re-extraer el CV entero la próxima vez.
    """
    enviado = {
        "anios_experiencia": anios_experiencia,
        "titulo_actual": titulo_actual,
        "ubicacion": ubicacion,
        "resumen": resumen,
        "roles": roles,
        "sectores": sectores,
        "idiomas": idiomas,
        "formacion": formacion,
        "certificaciones": certificaciones,
        "skills": skills,
    }

    try:
        perfil = PerfilCandidato(
            anios_experiencia=_numero(anios_experiencia, "Los años de experiencia"),
            titulo_actual=titulo_actual.strip() or None,
            roles=_lista(roles),
            skills=_skills_desde_texto(skills),
            sectores=_lista(sectores),
            idiomas=_lista(idiomas),
            formacion=_lista(formacion),
            certificaciones=_lista(certificaciones),
            ubicacion=ubicacion.strip() or None,
            resumen=resumen.strip(),
        )
    except ValorNoValido as e:
        return _pagina_perfil(
            request, sesion, error=str(e), formulario=enviado, codigo=status.HTTP_400_BAD_REQUEST
        )

    fila = perfil_vigente(sesion)
    if fila is None:
        fila = Perfil(datos=perfil.model_dump())
        sesion.add(fila)
    else:
        fila.datos = perfil.model_dump()
    fila.editado_a_mano = True
    fila.actualizado_en = ahora()
    sesion.commit()

    return _pagina_perfil(
        request,
        sesion,
        aviso=(
            "Perfil guardado y marcado como editado a mano. Si mañana subes un CV distinto, "
            "estas correcciones no se arrastran: quedarán en el histórico."
        ),
    )


# --------------------------------------------------------------------------
# Preferencias
# --------------------------------------------------------------------------


def _fila_preferencias(sesion: Session) -> PreferenciasRow | None:
    """La fila que lee el pipeline: la más reciente."""
    return sesion.scalar(select(PreferenciasRow).order_by(PreferenciasRow.id.desc()))


def preferencias_vigentes(sesion: Session) -> Preferencias:
    fila = _fila_preferencias(sesion)
    return Preferencias.model_validate(fila.datos) if fila and fila.datos else Preferencias()


def _formulario_preferencias(prefs: Preferencias) -> dict:
    return {
        "salario_min": "" if prefs.salario_min is None else _sin_cola(prefs.salario_min),
        "modalidades": list(prefs.modalidades),
        "zonas": ", ".join(prefs.zonas),
        "sectores_veto": ", ".join(prefs.sectores_veto),
        "tecnologias_veto": ", ".join(prefs.tecnologias_veto),
        "idiomas": list(prefs.idiomas),
        "notas": prefs.notas,
    }


def _pagina_preferencias(
    request: Request,
    sesion: Session,
    *,
    aviso: str | None = None,
    error: str | None = None,
    formulario: dict | None = None,
    codigo: int = status.HTTP_200_OK,
) -> HTMLResponse:
    prefs = preferencias_vigentes(sesion)
    return get_plantillas().TemplateResponse(
        request,
        "preferencias.html",
        {
            "titulo": "Preferencias",
            "formulario": formulario or _formulario_preferencias(prefs),
            "modalidades_posibles": MODALIDADES,
            "idiomas_posibles": IDIOMAS,
            # Cuántas ofertas hay ahora mismo fuera de la cola por una regla. Es lo
            # que da sentido al botón de reevaluar: si son cero, no hay nada que
            # recuperar; si son muchas, conviene mirarlas.
            "descartadas_por_regla": len(
                sesion.scalars(
                    select(Job.id).where(
                        Job.estado_clasificacion == ESTADO_DESCARTADA_POR_REGLA
                    )
                ).all()
            ),
            "aviso": aviso,
            "error": error,
        },
        status_code=codigo,
    )


@router.get("/preferences", response_class=HTMLResponse)
def ver_preferencias(request: Request, sesion: Session = Depends(get_sesion)) -> HTMLResponse:
    return _pagina_preferencias(request, sesion)


@router.post("/preferences", response_class=HTMLResponse)
def guardar_preferencias(
    request: Request,
    sesion: Session = Depends(get_sesion),
    salario_min: str = Form(""),
    modalidades: list[str] = Form([]),  # noqa: B006 - FastAPI no muta el valor por defecto
    zonas: str = Form(""),
    sectores_veto: str = Form(""),
    tecnologias_veto: str = Form(""),
    idiomas: list[str] = Form([]),  # noqa: B006
    notas: str = Form(""),
) -> HTMLResponse:
    enviado = {
        "salario_min": salario_min,
        "modalidades": modalidades,
        "zonas": zonas,
        "sectores_veto": sectores_veto,
        "tecnologias_veto": tecnologias_veto,
        "idiomas": idiomas,
        "notas": notas,
    }

    try:
        minimo = _numero(salario_min, "El salario mínimo")
    except ValorNoValido as e:
        # No se guarda nada: rechazar a medias dejaría unas preferencias que el
        # usuario cree haber puesto y en realidad no están.
        return _pagina_preferencias(
            request,
            sesion,
            error=str(e),
            formulario=enviado,
            codigo=status.HTTP_400_BAD_REQUEST,
        )

    prefs = Preferencias(
        salario_min=minimo,
        modalidades=_solo_conocidos(modalidades, MODALIDADES),
        zonas=_lista(zonas),
        sectores_veto=_lista(sectores_veto),
        tecnologias_veto=_lista(tecnologias_veto),
        idiomas=_solo_conocidos(idiomas, IDIOMAS),
        notas=notas.strip(),
    )

    fila = _fila_preferencias(sesion)
    if fila is None:
        fila = PreferenciasRow(datos=prefs.model_dump())
        sesion.add(fila)
    else:
        fila.datos = prefs.model_dump()
    fila.actualizado_en = ahora()
    sesion.commit()

    return _pagina_preferencias(
        request,
        sesion,
        aviso=(
            "Preferencias guardadas. Si has quitado algún veto, reevalúa el prefiltro: "
            "las ofertas que descartó esa regla siguen fuera de la cola hasta que lo hagas."
        ),
    )


@router.post("/preferences/reevaluar", response_class=HTMLResponse)
def reevaluar_prefiltro(
    request: Request, sesion: Session = Depends(get_sesion)
) -> HTMLResponse:
    """Vuelve a pasar las reglas actuales por lo que descartó una regla anterior.

    Sólo se miran las ofertas en `descartada_por_regla`. Las clasificadas ya costaron
    una llamada al modelo y las que están en `error` agotaron sus intentos: el
    prefiltro no tiene nada que decir sobre ninguna de las dos.
    """
    prefs = preferencias_vigentes(sesion)
    descartadas = sesion.scalars(
        select(Job).where(Job.estado_clasificacion == ESTADO_DESCARTADA_POR_REGLA)
    ).all()

    devueltas = 0
    for job in descartadas:
        resultado = aplica_prefiltro(job_a_rawjob(job), prefs)
        if resultado.descartada:
            # El motivo se refresca: si ahora la descarta otra regla, la vista de
            # descartes tiene que decir cuál, no la de ayer.
            job.motivo_regla = resultado.motivo
            continue
        job.estado_clasificacion = ESTADO_PENDIENTE
        job.motivo_regla = None
        devueltas += 1
    sesion.commit()

    vuelven = (
        "1 oferta vuelve a la cola" if devueltas == 1 else f"{devueltas} ofertas vuelven a la cola"
    )
    return _pagina_preferencias(
        request,
        sesion,
        aviso=(
            f"Reevaluadas {len(descartadas)} ofertas descartadas por regla: {vuelven}. "
            "Se clasificarán en la próxima ejecución."
        ),
    )


# --------------------------------------------------------------------------
# Búsquedas guardadas
# --------------------------------------------------------------------------


def creditos_por_run(sesion: Session, paginas: int = 1) -> int:
    """Créditos de JSearch que gasta un run con las búsquedas activas de ahora.

    JSearch cobra por petición y pide una petición por página y búsqueda, así que el
    coste es el número de búsquedas activas que la incluyen por las páginas pedidas.
    Las inactivas no cuentan porque el run no las ejecuta.
    """
    filas = sesion.scalars(select(BusquedaGuardada).where(BusquedaGuardada.activa)).all()
    return sum(paginas for fila in filas if "jsearch" in (fila.fuentes or []))


def coste_jsearch(sesion: Session) -> dict:
    """Lo que comprometen las búsquedas activas frente al cupo mensual.

    Es el recurso escaso del sistema: límite duro y sin aviso previo. Verlo antes de
    marcar la casilla es lo que evita quedarse sin cupo a mitad de mes.
    """
    from app.config import get_settings

    settings = get_settings()
    por_run = creditos_por_run(sesion, settings.jsearch_paginas)
    al_mes = por_run * RUNS_AL_MES
    return {
        "por_run": por_run,
        "al_mes": al_mes,
        "limite": settings.jsearch_limite_mensual,
        "paginas": settings.jsearch_paginas,
        "runs_al_mes": RUNS_AL_MES,
        "excede": al_mes > settings.jsearch_limite_mensual,
    }


class LimitadorDeRuns:
    """Cuánto hay que esperar entre dos runs lanzados a mano.

    Vive en memoria del proceso y no en la base de datos a propósito: es una
    herramienta local y monousuaria, y persistirlo no aportaría nada que la ventana
    de "run en curso" no cubra ya tras un reinicio.
    """

    def __init__(
        self,
        reloj: Callable[[], datetime] | None = None,
        intervalo: timedelta = INTERVALO_MINIMO_ENTRE_RUNS,
    ) -> None:
        self._reloj = reloj or _ahora_naive
        self.intervalo = intervalo
        self.ultimo: datetime | None = None

    def ahora(self) -> datetime:
        return self._reloj()

    def espera_restante(self) -> timedelta:
        if self.ultimo is None:
            return timedelta(0)
        restante = self.intervalo - (self.ahora() - self.ultimo)
        return restante if restante > timedelta(0) else timedelta(0)

    def intenta_lanzar(self) -> bool:
        """Concede el turno y lo anota, o lo deniega. Nunca lo concede a medias."""
        if self.espera_restante() > timedelta(0):
            return False
        self.ultimo = self.ahora()
        return True


def get_limitador_runs(request: Request) -> LimitadorDeRuns:
    """Limitador compartido por toda la aplicación, creado al primer uso.

    Cuelga de `app.state` y no de una variable de módulo para que cada aplicación
    (cada test crea la suya con `crear_app()`) tenga el suyo y no herede el estado
    de otra.
    """
    limitador = getattr(request.app.state, "limitador_runs", None)
    if limitador is None:
        limitador = LimitadorDeRuns()
        request.app.state.limitador_runs = limitador
    return limitador


def lanza_en_segundo_plano(objetivo: Callable[[], None]) -> threading.Thread:
    """Arranca `objetivo` en un hilo y devuelve enseguida.

    Devuelve el hilo para poder comprobar en un test que la llamada no espera a que
    termine. Es demonio: cerrar la web no debe quedarse colgada esperando un run.
    """

    def envuelto() -> None:
        try:
            objetivo()
        except Exception:  # noqa: BLE001 - nadie recogería la excepción de un hilo suelto
            traceback.print_exc()

    hilo = threading.Thread(target=envuelto, name="run-manual", daemon=True)
    hilo.start()
    return hilo


def _ejecuta_run_completo() -> None:
    """El run de verdad, con su propia sesión.

    Se abre una sesión nueva y no la de la petición porque la petición ya habrá
    terminado cuando esto empiece, y una sesión de SQLAlchemy no se comparte entre
    hilos. Los importes van dentro para que este módulo se pueda importar sin tocar
    la base de datos ni cargar los SDK.
    """
    from app.cli import _busquedas_activas, construye_fuentes
    from app.config import get_settings
    from app.db import crear_engine, crear_sesion, crear_tablas
    from app.llm.factory import crear_provider
    from app.pipeline import ejecuta_run

    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)
    with crear_sesion(engine) as sesion:
        queries, nombres_fuentes = _busquedas_activas(sesion)
        if not queries:
            return
        ejecuta_run(
            sesion,
            fuentes=construye_fuentes(nombres_fuentes, settings, sesion),
            queries=queries,
            provider=crear_provider(settings),
            max_clasificaciones=settings.max_clasificaciones_por_run,
        )


def lanzar_run() -> None:
    """Lanza un run completo sin bloquear a quien llama."""
    lanza_en_segundo_plano(_ejecuta_run_completo)


def get_lanzador_run() -> Callable[[], None]:
    """Cómo se lanza un run. Sustituible en tests: ninguno ejecuta el de verdad."""
    return lanzar_run


def _hay_run_en_curso(sesion: Session, ahora_: datetime) -> bool:
    """Un run abierto y reciente. Los viejos sin cerrar se dan por muertos."""
    run = sesion.scalar(select(Run).where(Run.fin.is_(None)).order_by(Run.inicio.desc()))
    if run is None or run.inicio is None:
        return False
    return (ahora_ - run.inicio) < VENTANA_RUN_EN_CURSO


def _minutos(espera: timedelta) -> int:
    return max(1, int(espera.total_seconds() // 60))


def _formulario_busqueda(fila: BusquedaGuardada | None = None) -> dict:
    if fila is None:
        return {
            "nombre": "",
            "texto": "",
            "pais": "es",
            "ubicacion": "",
            "solo_remoto": False,
            "fuentes": ["remotive"],
            "activa": True,
        }
    return {
        "nombre": fila.nombre,
        "texto": fila.texto,
        "pais": fila.pais,
        "ubicacion": fila.ubicacion or "",
        "solo_remoto": fila.solo_remoto,
        "fuentes": list(fila.fuentes or []),
        "activa": fila.activa,
    }


def _pagina_busquedas(
    request: Request,
    sesion: Session,
    *,
    aviso: str | None = None,
    error: str | None = None,
    formulario: dict | None = None,
    codigo: int = status.HTTP_200_OK,
) -> HTMLResponse:
    busquedas = sesion.scalars(select(BusquedaGuardada).order_by(BusquedaGuardada.id)).all()
    return get_plantillas().TemplateResponse(
        request,
        "busquedas.html",
        {
            "titulo": "Búsquedas",
            "busquedas": busquedas,
            "formularios": {b.id: _formulario_busqueda(b) for b in busquedas},
            "formulario": formulario or _formulario_busqueda(),
            "fuentes_posibles": FUENTES_DISPONIBLES,
            "coste": coste_jsearch(sesion),
            "aviso": aviso,
            "error": error,
        },
        status_code=codigo,
    )


@router.get("/searches", response_class=HTMLResponse)
def ver_busquedas(request: Request, sesion: Session = Depends(get_sesion)) -> HTMLResponse:
    return _pagina_busquedas(request, sesion)


def _valida(nombre: str, texto: str) -> str | None:
    if not nombre.strip():
        return "La búsqueda necesita un nombre."
    if not texto.strip():
        return "La búsqueda necesita un texto que buscar."
    return None


@router.post("/searches", response_class=HTMLResponse)
def crear_busqueda(
    request: Request,
    sesion: Session = Depends(get_sesion),
    nombre: str = Form(""),
    texto: str = Form(""),
    pais: str = Form("es"),
    ubicacion: str = Form(""),
    solo_remoto: bool = Form(False),
    fuentes: list[str] = Form([]),  # noqa: B006
    activa: bool = Form(False),
) -> HTMLResponse:
    fallo = _valida(nombre, texto)
    if fallo:
        return _pagina_busquedas(
            request,
            sesion,
            error=fallo,
            formulario={
                "nombre": nombre,
                "texto": texto,
                "pais": pais,
                "ubicacion": ubicacion,
                "solo_remoto": solo_remoto,
                "fuentes": fuentes,
                "activa": activa,
            },
            codigo=status.HTTP_400_BAD_REQUEST,
        )

    sesion.add(
        BusquedaGuardada(
            nombre=nombre.strip(),
            texto=texto.strip(),
            pais=pais.strip() or "es",
            ubicacion=ubicacion.strip() or None,
            solo_remoto=solo_remoto,
            fuentes=_solo_conocidos(fuentes, FUENTES_DISPONIBLES),
            activa=activa,
        )
    )
    sesion.commit()
    return _pagina_busquedas(request, sesion, aviso=f"Búsqueda «{nombre.strip()}» creada.")


# `/searches/buscar` se declara ANTES que `/searches/{busqueda_id}`: FastAPI resuelve
# por orden de registro y, al revés, 'buscar' entraría por la ruta con parámetro y se
# quedaría en un 422 por no ser un número.
@router.post("/searches/buscar", response_class=HTMLResponse)
def buscar_ahora(
    request: Request,
    sesion: Session = Depends(get_sesion),
    lanzador: Callable[[], None] = Depends(get_lanzador_run),
    limitador: LimitadorDeRuns = Depends(get_limitador_runs),
) -> HTMLResponse:
    """Lanza un run a mano, sin bloquear la petición y sin permitir ráfagas."""
    if _hay_run_en_curso(sesion, limitador.ahora()):
        return _pagina_busquedas(
            request,
            sesion,
            aviso=(
                "Ya hay un run en curso. Espera a que termine: verás el resultado en "
                "Ejecuciones."
            ),
        )

    if not limitador.intenta_lanzar():
        return _pagina_busquedas(
            request,
            sesion,
            aviso=(
                f"Espera {_minutos(limitador.espera_restante())} minutos antes de volver a "
                "buscar. Las fuentes gratuitas piden un máximo de unas cuatro peticiones "
                "al día y el run diario ya trae lo nuevo."
            ),
        )

    lanzador()
    return _pagina_busquedas(
        request,
        sesion,
        aviso=(
            "Run lanzado en segundo plano. Tarda varios minutos; el progreso y los "
            "errores se ven en Ejecuciones."
        ),
    )


@router.post("/searches/{busqueda_id}", response_class=HTMLResponse)
def editar_busqueda(
    request: Request,
    busqueda_id: int,
    sesion: Session = Depends(get_sesion),
    nombre: str = Form(""),
    texto: str = Form(""),
    pais: str = Form("es"),
    ubicacion: str = Form(""),
    solo_remoto: bool = Form(False),
    fuentes: list[str] = Form([]),  # noqa: B006
    activa: bool = Form(False),
) -> HTMLResponse:
    """Actualiza la búsqueda existente.

    `carga_semilla()` se salta las búsquedas cuyo nombre ya existe y por eso editar el
    YAML no cambia nada. Aquí se actualiza la fila entera, incluido el nombre.
    """
    fila = sesion.get(BusquedaGuardada, busqueda_id)
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Búsqueda no encontrada")

    fallo = _valida(nombre, texto)
    if fallo:
        return _pagina_busquedas(request, sesion, error=fallo, codigo=status.HTTP_400_BAD_REQUEST)

    fila.nombre = nombre.strip()
    fila.texto = texto.strip()
    fila.pais = pais.strip() or "es"
    fila.ubicacion = ubicacion.strip() or None
    fila.solo_remoto = solo_remoto
    fila.fuentes = _solo_conocidos(fuentes, FUENTES_DISPONIBLES)
    fila.activa = activa
    sesion.commit()

    return _pagina_busquedas(request, sesion, aviso=f"Búsqueda «{fila.nombre}» actualizada.")


@router.post("/searches/{busqueda_id}/borrar", response_class=HTMLResponse)
def borrar_busqueda(
    request: Request, busqueda_id: int, sesion: Session = Depends(get_sesion)
) -> HTMLResponse:
    fila = sesion.get(BusquedaGuardada, busqueda_id)
    if fila is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Búsqueda no encontrada")

    nombre = fila.nombre
    sesion.delete(fila)
    sesion.commit()
    return _pagina_busquedas(request, sesion, aviso=f"Búsqueda «{nombre}» borrada.")
