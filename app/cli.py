import argparse
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import crear_engine, crear_sesion, crear_tablas
from app.llm.factory import crear_provider
from app.models import BusquedaGuardada, Perfil, PreferenciasRow
from app.pipeline import ejecuta_run
from app.profile import crear_cliente, extrae_perfil
from app.schemas import SearchQuery


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


def construye_fuentes(nombres: list[str], settings: Settings) -> list:
    """Construye las fuentes solicitadas, saltando las que no tienen credenciales.

    Adzuna sin claves no debe tumbar el run: las otras dos siguen funcionando.
    """
    fuentes = []
    for nombre in dict.fromkeys(nombres):
        if nombre == "adzuna":
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
    cliente = crear_cliente(settings.gemini_api_key)
    perfil = extrae_perfil(pdf, cliente=cliente, modelo=settings.modelo_gemini)

    with crear_sesion(engine) as sesion:
        sesion.add(Perfil(ruta_pdf=str(args.pdf), datos=perfil.model_dump()))
        sesion.commit()

    print(f"Perfil extraído: {perfil.anios_experiencia} años, {len(perfil.skills)} skills")
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
            fuentes=construye_fuentes(nombres_fuentes, settings),
            queries=queries,
            provider=crear_provider(settings),
            max_clasificaciones=settings.max_clasificaciones_por_run,
        )
        print(f"Run {run.id}: {run.stats.get('_totales')}")
        if run.errores:
            print(f"Errores: {run.errores}")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
