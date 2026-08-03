from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Clasificacion, Decision, Job, Run


def test_se_persiste_una_oferta(sesion):
    job = Job(
        fuente="remotive",
        external_id="2091081",
        url="https://remotive.com/x",
        titulo="Senior Backend",
        empresa="Acme",
        descripcion="texto",
        hash_dedup="abc123",
        estado_clasificacion="pendiente",
    )
    sesion.add(job)
    sesion.commit()

    assert job.id is not None
    assert job.ingerida_en is not None


def test_el_hash_dedup_es_unico(sesion):
    for _ in range(2):
        sesion.add(
            Job(
                fuente="remotive",
                external_id="1",
                url="u",
                titulo="t",
                empresa="e",
                descripcion="d",
                hash_dedup="mismo",
                estado_clasificacion="pendiente",
            )
        )

    with pytest.raises(IntegrityError):
        sesion.commit()


def test_una_oferta_tiene_clasificacion_y_decision(sesion):
    job = Job(
        fuente="adzuna",
        external_id="9",
        url="u",
        titulo="t",
        empresa="e",
        descripcion="d",
        hash_dedup="h9",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.flush()

    sesion.add(
        Clasificacion(
            job_id=job.id,
            categoria="aplicar_ya",
            confianza="alta",
            razonamiento="Encaja.",
            ejes={"tecnico": "bien"},
            skills_faltantes=["kubernetes"],
            red_flags=[],
            modelo="gemini-2.5-flash",
            prompt_version=1,
        )
    )
    sesion.add(Decision(job_id=job.id, estado="guardada", motivo="Stack que quiero"))
    sesion.commit()

    assert job.clasificacion.categoria == "aplicar_ya"
    assert job.decision.motivo == "Stack que quiero"


def crea_job(sesion, sufijo: str) -> Job:
    job = Job(
        fuente="test",
        external_id=sufijo,
        url=f"https://example.com/{sufijo}",
        titulo="Backend",
        empresa="Acme",
        descripcion="d",
        hash_dedup=f"hash{sufijo}",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.flush()
    return job


@pytest.mark.parametrize(
    "estado",
    ["guardada", "aplicada", "en_proceso", "rechazado_por_ellos", "descartada_por_mi"],
)
def test_cada_estado_de_decision_se_persiste_y_se_lee_de_vuelta(sesion, estado):
    job = crea_job(sesion, estado)

    sesion.add(Decision(job_id=job.id, estado=estado, motivo="porque sí"))
    sesion.commit()

    assert job.decision.estado == estado


def test_una_decision_nace_sin_fecha_de_aplicacion_y_con_fecha_de_actualizacion(sesion):
    """`aplicada_en` sólo tiene sentido cuando uno se ha presentado de verdad;
    ponerle una fecha de oficio falsearía el recuento del mes."""
    job = crea_job(sesion, "1")

    sesion.add(Decision(job_id=job.id, estado="guardada", motivo="para luego"))
    sesion.commit()

    assert job.decision.aplicada_en is None
    assert job.decision.actualizada_en is not None


def test_una_decision_guarda_la_fecha_en_que_uno_se_presento(sesion):
    job = crea_job(sesion, "1")

    sesion.add(
        Decision(
            job_id=job.id,
            estado="aplicada",
            motivo="me presenté",
            aplicada_en=datetime(2026, 5, 12),
        )
    )
    sesion.commit()

    assert job.decision.aplicada_en == datetime(2026, 5, 12)


def test_una_oferta_no_puede_tener_dos_decisiones(sesion):
    """La interfaz decide dos veces sobre la misma oferta constantemente: la
    unicidad es lo que obliga a actualizar en vez de insertar a ciegas."""
    job = crea_job(sesion, "1")
    sesion.add(Decision(job_id=job.id, estado="guardada", motivo="a"))
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="b"))

    with pytest.raises(IntegrityError):
        sesion.commit()


def test_un_run_guarda_estadisticas_y_errores(sesion):
    run = Run(
        inicio=datetime(2026, 8, 3, 7, 0),
        fin=datetime(2026, 8, 3, 7, 4),
        stats={"remotive": {"recibidas": 31, "nuevas": 4}},
        errores=[{"fuente": "adzuna", "error": "HTTP 400"}],
    )
    sesion.add(run)
    sesion.commit()

    assert run.stats["remotive"]["nuevas"] == 4
    assert run.errores[0]["fuente"] == "adzuna"
