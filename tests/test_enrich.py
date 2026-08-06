from datetime import datetime

from app.enrich import MAX_INTENTOS_SCRAPE, pendientes_de_enriquecer
from app.models import Job


def crea_job(sesion, external_id="1", **kwargs) -> Job:
    base = dict(
        fuente="adzuna",
        external_id=external_id,
        url=f"https://www.adzuna.es/details/{external_id}",
        titulo="Backend Developer",
        empresa="Empresa",
        ubicacion="Sevilla",
        descripcion="Extracto corto de la oferta…",
        descripcion_truncada=True,
        hash_dedup=f"hash-{external_id}",
        ingerida_en=datetime(2026, 8, 1, 10, 0),
    )
    base.update(kwargs)
    job = Job(**base)
    sesion.add(job)
    sesion.commit()
    return job


def test_coge_las_ofertas_truncadas_de_adzuna(sesion):
    crea_job(sesion, "1")

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 10)] == ["1"]


def test_ignora_las_ofertas_ya_completas(sesion):
    crea_job(sesion, "1", descripcion_truncada=False)

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_ignora_las_de_otras_fuentes(sesion):
    crea_job(sesion, "1", fuente="scrappa")

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_ignora_las_que_ya_agotaron_los_intentos(sesion):
    crea_job(sesion, "1", intentos_scrape=MAX_INTENTOS_SCRAPE)

    assert pendientes_de_enriquecer(sesion, 10) == []


def test_coge_las_filas_heredadas_con_intentos_a_null(sesion):
    """La regresión que la migración deja servida en bandeja.

    `asegura_esquema()` añade `intentos_scrape` SIN valor por defecto, así que las 136
    ofertas del atraso la tienen a NULL. En SQL, `NULL < 3` es NULL, que no es
    verdadero: un `WHERE intentos_scrape < 3` las dejaría fuera y el atraso entero sería
    invisible, sin ningún error a la vista.
    """
    job = crea_job(sesion, "1")
    job.intentos_scrape = None
    sesion.commit()

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 10)] == ["1"]


def test_respeta_el_tope(sesion):
    for i in range(5):
        crea_job(sesion, str(i))

    assert len(pendientes_de_enriquecer(sesion, 2)) == 2


def test_empieza_por_lo_mas_recien_ingerido(sesion):
    """Con 136 de atraso y un tope de 40, el orden ascendente haría que las ofertas de
    hoy —las que se van a clasificar en este mismo run— esperasen cuatro días."""
    crea_job(sesion, "vieja", ingerida_en=datetime(2026, 8, 1, 10, 0))
    crea_job(sesion, "nueva", ingerida_en=datetime(2026, 8, 6, 10, 0))

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 1)] == ["nueva"]
