import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import crear_engine, crear_sesion, crear_tablas
from app.limitador import LimitadorPorHost
from app.llm.factory import crear_provider
from app.models import BusquedaGuardada, PreferenciasRow
from app.pipeline import ejecuta_run
from app.profile import (
    PDF_NUEVO_CON_EDICION_PREVIA,
    SIN_CAMBIOS,
    crear_cliente,
    extrae_perfil,
    sincroniza_perfil,
)
from app.schemas import PerfilCandidato, SearchQuery


def carga_semilla(sesion: Session, ruta: Path) -> None:
    """Crea preferencias y búsquedas desde un YAML. Idempotente por nombre de búsqueda."""
    datos = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}

    fila_prefs = sesion.scalar(select(PreferenciasRow))
    if fila_prefs is None:
        sesion.add(PreferenciasRow(datos=datos.get("preferencias", {})))
    else:
        fila_prefs.datos = datos.get("preferencias", {})

    existentes = {b.nombre for b in sesion.scalars(select(BusquedaGuardada)).all()}
    for busqueda in datos.get("busquedas", []):
        if busqueda["nombre"] in existentes:
            continue
        sesion.add(
            BusquedaGuardada(
                nombre=busqueda["nombre"],
                texto=busqueda["texto"],
                pais=busqueda.get("pais", "es"),
                ubicacion=busqueda.get("ubicacion"),
                solo_remoto=busqueda.get("solo_remoto", False),
                fuentes=busqueda.get("fuentes", []),
            )
        )

    sesion.commit()


def construye_fuentes(
    nombres: list[str], settings: Settings, sesion: Session | None = None
) -> list:
    """Construye las fuentes solicitadas, saltando las que no tienen credenciales.

    Adzuna sin claves no debe tumbar el run: las demás siguen funcionando.

    JSearch necesita además una sesión, porque su cupo mensual vive en la base de
    datos. Sin sesión se salta en vez de construirse sin presupuesto: una fuente de
    cupo duro sin contador se lo gasta entero y deja de servir a mitad de mes.
    """
    fuentes = []
    for nombre in dict.fromkeys(nombres):
        if nombre == "scrappa":
            if not settings.scrappa_api_key or sesion is None:
                continue
            from app.presupuesto import PresupuestoMensual
            from app.sources.scrappa import ScrappaSource

            fuentes.append(
                ScrappaSource(
                    api_key=settings.scrappa_api_key,
                    resultados=settings.scrappa_resultados,
                    presupuesto=PresupuestoMensual(
                        sesion, "scrappa", limite=settings.scrappa_limite_mensual
                    ),
                )
            )
        elif nombre == "jsearch":
            if not settings.jsearch_api_key or sesion is None:
                continue
            from app.presupuesto import PresupuestoMensual
            from app.sources.jsearch import JSearchSource

            fuentes.append(
                JSearchSource(
                    api_key=settings.jsearch_api_key,
                    paginas=settings.jsearch_paginas,
                    presupuesto=PresupuestoMensual(
                        sesion, "jsearch", limite=settings.jsearch_limite_mensual
                    ),
                )
            )
        elif nombre == "adzuna":
            if not (settings.adzuna_app_id and settings.adzuna_app_key):
                continue
            from app.sources.adzuna import AdzunaSource

            fuentes.append(
                AdzunaSource(app_id=settings.adzuna_app_id, app_key=settings.adzuna_app_key)
            )
        elif nombre == "remotive":
            from app.sources.remotive import RemotiveSource

            fuentes.append(RemotiveSource())
        elif nombre == "arbeitnow":
            from app.sources.arbeitnow import ArbeitnowSource

            fuentes.append(ArbeitnowSource(max_paginas=2))

    return fuentes


def construye_enriquecedor(settings: Settings) -> Callable[[str], str] | None:
    """El lector de fichas de Adzuna, o None si está apagado.

    Devolver None es lo mismo que no pasar el parámetro a `ejecuta_run()`: el
    interruptor de configuración y el valor por defecto del pipeline son la misma cosa
    vista desde los dos lados.

    El limitador es propio y por host: `www.adzuna.es` no es `api.adzuna.com`, así que
    no compite con el de la API.
    """
    if not settings.adzuna_scrape_activo:
        return None

    from app.sources.adzuna_web import INTERVALO_SEGUNDOS, descarga_descripcion

    limitador = LimitadorPorHost(intervalo_por_defecto=INTERVALO_SEGUNDOS)

    def enriquecedor(url: str) -> str:
        return descarga_descripcion(
            url, limitador=limitador, timeout=settings.adzuna_scrape_timeout
        )

    return enriquecedor


def _busquedas_activas(sesion: Session) -> tuple[list[SearchQuery], list[str]]:
    filas = sesion.scalars(select(BusquedaGuardada).where(BusquedaGuardada.activa)).all()
    queries = [
        SearchQuery(
            nombre=f.nombre,
            texto=f.texto,
            pais=f.pais,
            ubicacion=f.ubicacion,
            solo_remoto=f.solo_remoto,
        )
        for f in filas
    ]
    nombres_fuentes = [n for f in filas for n in (f.fuentes or [])]
    return queries, nombres_fuentes


def comando_init(args) -> int:
    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)
    with crear_sesion(engine) as sesion:
        if args.semilla:
            carga_semilla(sesion, Path(args.semilla))
    print(f"Base de datos lista en {settings.ruta_bd}")
    return 0


def comando_cv(args) -> int:
    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)

    pdf = Path(args.pdf).read_bytes()

    def extrae() -> PerfilCandidato:
        # Perezoso a propósito: si el PDF no ha cambiado no se llama al modelo, y
        # entonces tampoco hace falta GEMINI_API_KEY.
        cliente = crear_cliente(settings.gemini_api_key)
        return extrae_perfil(pdf, cliente=cliente, modelo=settings.modelo_perfil)

    with crear_sesion(engine) as sesion:
        resultado = sincroniza_perfil(sesion, pdf, ruta=str(args.pdf), extractor=extrae)

    perfil = resultado.perfil
    resumen = f"{perfil.anios_experiencia} años, {len(perfil.skills)} skills"

    if resultado.motivo == SIN_CAMBIOS:
        print(f"El CV no ha cambiado: se conserva el perfil guardado ({resumen})")
        return 0

    if resultado.motivo == PDF_NUEVO_CON_EDICION_PREVIA:
        print(
            "Aviso: el perfil anterior tenía correcciones manuales. El CV es distinto, "
            "así que se ha vuelto a extraer y las correcciones manuales no se arrastran; "
            "el perfil anterior se conserva en el histórico. Revísalo en /profile."
        )

    print(f"Perfil extraído: {resumen}")
    return 0


def comando_run(args) -> int:
    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)

    with crear_sesion(engine) as sesion:
        queries, nombres_fuentes = _busquedas_activas(sesion)
        if not queries:
            print("No hay búsquedas activas. Ejecuta 'init --semilla seed.yaml' primero.")
            return 1

        run = ejecuta_run(
            sesion,
            fuentes=construye_fuentes(nombres_fuentes, settings, sesion),
            queries=queries,
            provider=crear_provider(settings),
            enriquecedor=construye_enriquecedor(settings),
            max_scrapes=settings.adzuna_scrape_max_por_run,
            max_clasificaciones=settings.max_clasificaciones_por_run,
        )
        print(f"Run {run.id}: {run.stats.get('_totales')}")
        if run.errores:
            print(f"Errores: {run.errores}")
    return 0


def comando_reclasificar(args) -> int:
    """Devuelve a la cola las ofertas ya juzgadas. No las clasifica.

    Se separa del run a propósito: marcar es instantáneo y barato, mientras que
    clasificar cuesta dinero y tiempo. Verlas marcadas antes de lanzar el run da la
    oportunidad de arrepentirse.
    """
    from app.reclasifica import marca_para_reclasificar

    settings = get_settings()
    engine = crear_engine(settings.ruta_bd)
    crear_tablas(engine)

    with crear_sesion(engine) as sesion:
        marcadas = marca_para_reclasificar(
            sesion, saltar_decididas=not args.incluir_decididas
        )

    tope = settings.max_clasificaciones_por_run
    print(f"{marcadas} ofertas devueltas a la cola.")
    if marcadas > tope:
        print(
            f"El tope por run es {tope}, así que harán falta "
            f"{-(-marcadas // tope)} runs, o subir MAX_CLASIFICACIONES_POR_RUN."
        )
    print("Lanza 'python -m app.cli run' para que se clasifiquen.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isthatmynewjob")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_init = sub.add_parser("init", help="Crea las tablas y carga la semilla")
    p_init.add_argument("--semilla", help="Ruta a un YAML de preferencias y búsquedas")
    p_init.set_defaults(func=comando_init)

    p_cv = sub.add_parser("cv", help="Extrae el perfil desde un PDF")
    p_cv.add_argument("pdf", help="Ruta al PDF del currículum")
    p_cv.set_defaults(func=comando_cv)

    p_run = sub.add_parser("run", help="Ejecuta el pipeline completo")
    p_run.set_defaults(func=comando_run)

    p_recl = sub.add_parser(
        "reclasificar", help="Devuelve a la cola las ofertas ya juzgadas"
    )
    p_recl.add_argument(
        "--incluir-decididas",
        action="store_true",
        help="Rehace también las ofertas sobre las que ya decidiste a mano",
    )
    p_recl.set_defaults(func=comando_reclasificar)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
