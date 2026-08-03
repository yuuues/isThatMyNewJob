from datetime import datetime

from app.feedback import ejemplos_few_shot
from app.models import Decision, Job


def crea_job(sesion, sufijo: str, titulo: str = "Backend Developer") -> Job:
    job = Job(
        fuente="test",
        external_id=sufijo,
        url=f"https://example.com/{sufijo}",
        titulo=titulo,
        empresa=f"Empresa {sufijo}",
        descripcion="descripción de la oferta",
        hash_dedup=f"hash{sufijo}",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.flush()
    return job


def test_solo_devuelve_decisiones_con_motivo(sesion):
    con_motivo = crea_job(sesion, "1")
    sin_motivo = crea_job(sesion, "2")
    sesion.add(Decision(job_id=con_motivo.id, estado="interesa", motivo="Stack que quiero"))
    sesion.add(Decision(job_id=sin_motivo.id, estado="descartada", motivo=""))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    assert len(ejemplos) == 1
    assert ejemplos[0].motivo == "Stack que quiero"


def test_equilibra_ejemplos_positivos_y_negativos(sesion):
    for i in range(6):
        job = crea_job(sesion, f"p{i}")
        sesion.add(Decision(job_id=job.id, estado="interesa", motivo=f"me gusta {i}"))
    for i in range(2):
        job = crea_job(sesion, f"n{i}")
        sesion.add(Decision(job_id=job.id, estado="descartada", motivo=f"no me gusta {i}"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=6)

    positivos = [e for e in ejemplos if e.estado == "interesa"]
    negativos = [e for e in ejemplos if e.estado == "descartada"]
    assert len(ejemplos) == 6
    assert len(negativos) == 2
    assert len(positivos) == 4


def test_prioriza_las_decisiones_mas_recientes(sesion):
    antigua = crea_job(sesion, "vieja", titulo="Oferta antigua")
    reciente = crea_job(sesion, "nueva", titulo="Oferta reciente")
    sesion.add(
        Decision(
            job_id=antigua.id,
            estado="interesa",
            motivo="antigua",
            creada_en=datetime(2026, 1, 1),
        )
    )
    sesion.add(
        Decision(
            job_id=reciente.id,
            estado="interesa",
            motivo="reciente",
            creada_en=datetime(2026, 8, 1),
        )
    )
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=1)

    assert ejemplos[0].motivo == "reciente"


def test_aplicada_cuenta_como_ejemplo_positivo(sesion):
    job = crea_job(sesion, "a")
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="apliqué"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    assert ejemplos[0].positivo is True


def test_sin_decisiones_devuelve_lista_vacia(sesion):
    assert ejemplos_few_shot(sesion) == []
