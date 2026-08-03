"""Ofertas cerradas: el puesto ya no existe cuando abres el enlace.

Es un atributo de la OFERTA, no una decisión del candidato. Se puede haber aplicado y
que además la cierren, y esa combinación es justo la que interesa conservar.

Y por el mismo motivo que `rechazado_por_ellos`, una oferta cerrada no puede enseñar
nada al clasificador: que retiren un puesto no dice nada sobre lo que el candidato
quiere. Aquí ni siquiera hay riesgo, porque no es un estado de decisión, pero el test
lo fija por si algún día alguien lo convierte en uno.
"""

from datetime import datetime

import pytest
from sqlalchemy import select

from app.cerradas import cierra_oferta, cerradas_por_fuente, reabre_oferta
from app.decisiones import ESTADO_APLICADA, OfertaNoEncontrada, registra_decision
from app.feedback import ejemplos_few_shot
from app.models import Job


def crea(sesion, sufijo: str, fuente: str = "adzuna") -> Job:
    job = Job(
        fuente=fuente,
        external_id=sufijo,
        url=f"https://example.com/{sufijo}",
        titulo=f"Backend {sufijo}",
        empresa=f"Empresa {sufijo}",
        descripcion="descripción",
        hash_dedup=f"hash-{sufijo}",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.commit()
    return job


def test_cerrar_marca_la_oferta_con_su_fecha(sesion):
    job = crea(sesion, "1")

    cierra_oferta(sesion, job.id, ahora=lambda: datetime(2026, 8, 3, 12, 0))

    sesion.refresh(job)
    assert job.cerrada is True
    assert job.cerrada_en == datetime(2026, 8, 3, 12, 0)


def test_una_oferta_nace_abierta(sesion):
    assert crea(sesion, "1").cerrada is False


def test_reabrir_deshace_la_marca(sesion):
    job = crea(sesion, "1")
    cierra_oferta(sesion, job.id)

    reabre_oferta(sesion, job.id)

    sesion.refresh(job)
    assert job.cerrada is False
    assert job.cerrada_en is None


def test_cerrar_no_borra_la_decision(sesion):
    """El caso que motiva que sea un atributo y no un sexto estado: te presentaste y
    luego retiraron el puesto. Las dos cosas son ciertas a la vez."""
    job = crea(sesion, "1")
    registra_decision(sesion, job.id, ESTADO_APLICADA, motivo="me presenté")

    cierra_oferta(sesion, job.id)

    sesion.refresh(job)
    assert job.cerrada is True
    assert job.decision.estado == ESTADO_APLICADA
    assert job.decision.motivo == "me presenté"


def test_cerrar_una_oferta_inexistente_no_crea_nada(sesion):
    with pytest.raises(OfertaNoEncontrada):
        cierra_oferta(sesion, 999)

    assert sesion.scalars(select(Job)).all() == []


def test_cerrar_es_idempotente(sesion):
    job = crea(sesion, "1")
    cierra_oferta(sesion, job.id, ahora=lambda: datetime(2026, 8, 1, 9, 0))

    cierra_oferta(sesion, job.id, ahora=lambda: datetime(2026, 8, 5, 9, 0))

    sesion.refresh(job)
    assert job.cerrada_en == datetime(2026, 8, 1, 9, 0), "la primera fecha es la buena"


def test_cerrar_no_altera_los_ejemplos_del_clasificador(sesion):
    """Que retiren un puesto no dice nada sobre lo que el candidato quiere."""
    job = crea(sesion, "1")
    registra_decision(sesion, job.id, ESTADO_APLICADA, motivo="me encaja el stack")
    antes = ejemplos_few_shot(sesion)

    cierra_oferta(sesion, job.id)

    assert ejemplos_few_shot(sesion) == antes


def test_el_recuento_por_fuente_mide_que_portal_da_enlaces_muertos(sesion):
    """El motivo de que esto se cuente: si una fuente acumula cerradas, sus ofertas
    llegan tarde y conviene saberlo con datos, no por sensación."""
    for numero in range(3):
        cierra_oferta(sesion, crea(sesion, f"a{numero}", fuente="adzuna").id)
    cierra_oferta(sesion, crea(sesion, "s0", fuente="scrappa").id)
    crea(sesion, "s1", fuente="scrappa")

    recuento = cerradas_por_fuente(sesion)

    assert recuento["adzuna"] == {"cerradas": 3, "total": 3}
    assert recuento["scrappa"] == {"cerradas": 1, "total": 2}


def test_sin_ofertas_el_recuento_esta_vacio(sesion):
    assert cerradas_por_fuente(sesion) == {}
