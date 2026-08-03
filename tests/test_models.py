from datetime import datetime

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
    import pytest
    from sqlalchemy.exc import IntegrityError

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
    sesion.add(Decision(job_id=job.id, estado="interesa", motivo="Stack que quiero"))
    sesion.commit()

    assert job.clasificacion.categoria == "aplicar_ya"
    assert job.decision.motivo == "Stack que quiero"


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
