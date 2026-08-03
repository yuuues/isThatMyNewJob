"""El esquema evoluciona y las bases de datos existentes tienen que seguir arrancando.

Este módulo existe por un defecto real: la tabla `decision` ganó dos columnas y
`create_all()` no altera tablas ya creadas, así que sobre una base de datos con 248
ofertas y 194 clasificaciones el listado y el detalle respondían 500. La alternativa
que se descartó fue pedir al usuario que borrase su base de datos.
"""

import sqlite3

from sqlalchemy import create_engine, inspect, text

from app.db import asegura_esquema, crear_tablas


def columnas(ruta: str, tabla: str) -> set[str]:
    con = sqlite3.connect(ruta)
    try:
        return {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}
    finally:
        con.close()


def bd_con_esquema_viejo(tmp_path) -> str:
    """Reproduce la tabla `decision` tal como existía antes de los estados nuevos."""
    ruta = str(tmp_path / "vieja.db")
    con = sqlite3.connect(ruta)
    con.executescript(
        """
        CREATE TABLE decision (
            id INTEGER NOT NULL PRIMARY KEY,
            job_id INTEGER NOT NULL UNIQUE,
            estado VARCHAR NOT NULL,
            motivo TEXT,
            creada_en DATETIME
        );
        INSERT INTO decision (id, job_id, estado, motivo, creada_en)
        VALUES (1, 7, 'guardada', 'me interesa', '2026-05-01 10:00:00');
        """
    )
    con.commit()
    con.close()
    return ruta


def test_anade_las_columnas_que_faltan(tmp_path):
    ruta = bd_con_esquema_viejo(tmp_path)
    assert "aplicada_en" not in columnas(ruta, "decision")

    anadidas = asegura_esquema(create_engine(f"sqlite:///{ruta}"))

    assert "decision.aplicada_en" in anadidas
    assert "decision.actualizada_en" in anadidas
    assert {"aplicada_en", "actualizada_en"} <= columnas(ruta, "decision")


def test_conserva_las_filas_existentes(tmp_path):
    ruta = bd_con_esquema_viejo(tmp_path)

    asegura_esquema(create_engine(f"sqlite:///{ruta}"))

    con = sqlite3.connect(ruta)
    fila = con.execute("SELECT estado, motivo, aplicada_en FROM decision").fetchone()
    con.close()
    assert fila[0] == "guardada"
    assert fila[1] == "me interesa"
    assert fila[2] is None


def test_es_idempotente(tmp_path):
    ruta = bd_con_esquema_viejo(tmp_path)
    engine = create_engine(f"sqlite:///{ruta}")

    asegura_esquema(engine)
    segunda = asegura_esquema(engine)

    assert segunda == []


def test_sobre_una_base_al_dia_no_cambia_nada(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nueva.db'}")
    crear_tablas(engine)

    assert asegura_esquema(engine) == []


def test_ignora_las_tablas_que_todavia_no_existen(tmp_path):
    """`create_all` ya crea las tablas nuevas; esto sólo repara las viejas."""
    ruta = bd_con_esquema_viejo(tmp_path)
    engine = create_engine(f"sqlite:///{ruta}")

    asegura_esquema(engine)

    assert "job" not in set(inspect(engine).get_table_names())


def test_crear_tablas_repara_de_paso(tmp_path):
    """Cualquier punto de entrada (web, CLI, scheduler) pasa por aquí, así que la
    reparación tiene que ir donde ya pasan todos."""
    ruta = bd_con_esquema_viejo(tmp_path)
    engine = create_engine(f"sqlite:///{ruta}")

    crear_tablas(engine)

    assert {"aplicada_en", "actualizada_en"} <= columnas(ruta, "decision")
    assert "job" in set(inspect(engine).get_table_names())


def test_una_base_reparada_acepta_escrituras_del_modelo(tmp_path):
    """La prueba de fuego: que el ORM pueda escribir sobre la tabla reparada."""
    from datetime import datetime

    from app.db import crear_sesion
    from app.models import Decision

    ruta = bd_con_esquema_viejo(tmp_path)
    engine = create_engine(f"sqlite:///{ruta}")
    crear_tablas(engine)

    with crear_sesion(engine) as sesion:
        sesion.add(
            Decision(
                job_id=99,
                estado="aplicada",
                motivo="me presento",
                aplicada_en=datetime(2026, 8, 3, 12, 0),
            )
        )
        sesion.commit()
        guardada = sesion.execute(
            text("SELECT estado, aplicada_en FROM decision WHERE job_id = 99")
        ).first()

    assert guardada[0] == "aplicada"
