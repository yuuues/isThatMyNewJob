from sqlalchemy import select

from app.models import Clasificacion, Decision, Job
from app.reclasifica import marca_para_reclasificar


def crea_job(sesion, external_id="1", **kwargs) -> Job:
    base = dict(
        fuente="adzuna",
        external_id=external_id,
        url=f"https://www.adzuna.es/details/{external_id}",
        titulo="Backend Developer",
        empresa="Empresa",
        descripcion="Descripción completa de la oferta.",
        hash_dedup=f"hash-{external_id}",
        estado_clasificacion="clasificada",
    )
    base.update(kwargs)
    job = Job(**base)
    sesion.add(job)
    sesion.commit()
    return job


def crea_clasificacion(sesion, job) -> None:
    sesion.add(
        Clasificacion(
            job_id=job.id,
            categoria="aplicar_ya",
            confianza="alta",
            razonamiento="Juzgada con el prompt v2 y flash.",
            ejes={"tecnico": "ok", "seniority": "ok", "modalidad": "remoto",
                  "salario": "no publicado", "sector": "ok"},
            modelo="deepseek-v4-flash",
            prompt_version=2,
        )
    )
    sesion.commit()


def test_devuelve_la_oferta_a_la_cola_y_borra_el_veredicto(sesion):
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)

    marcadas = marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert marcadas == 1
    assert job.estado_clasificacion == "pendiente"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is None


def test_deja_los_intentos_de_clasificacion_a_cero(sesion):
    """Sin esto, devolver la oferta a la cola no la reabre: la entierra.

    Una oferta que agotó los tres intentos vuelve a "pendiente" y el bucle de
    `pipeline.py` la manda al estado terminal nada más sacarla, sin clasificarla ni una
    vez. `reintentar()` en app/web/routes_runs.py documenta la misma trampa.
    """
    job = crea_job(sesion, "1", intentos_clasificacion=3)
    crea_clasificacion(sesion, job)

    marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert job.intentos_clasificacion == 0


def test_limpia_el_motivo_de_regla(sesion):
    job = crea_job(
        sesion, "1", estado_clasificacion="descartada_por_regla",
        motivo_regla="zona fuera de rango: Madrid",
    )

    marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert job.motivo_regla is None
    assert job.estado_clasificacion == "pendiente"


def test_salta_las_ofertas_que_el_usuario_ya_decidio(sesion):
    """Reopinar sobre algo que ya cerró a mano no aporta nada y la reabre en la lista."""
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="Me presenté"))
    sesion.commit()

    marcadas = marca_para_reclasificar(sesion)

    sesion.refresh(job)
    assert marcadas == 0
    assert job.estado_clasificacion == "clasificada"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None


def test_puede_incluir_las_decididas_si_se_pide(sesion):
    job = crea_job(sesion, "1")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="Me presenté"))
    sesion.commit()

    marcadas = marca_para_reclasificar(sesion, saltar_decididas=False)

    sesion.refresh(job)
    assert marcadas == 1
    assert job.estado_clasificacion == "pendiente"


def test_no_toca_una_oferta_que_nunca_se_clasifico(sesion):
    """Ya está en la cola: marcarla otra vez no aporta nada y falsearía el recuento."""
    crea_job(sesion, "1", estado_clasificacion="pendiente")

    assert marca_para_reclasificar(sesion) == 0
