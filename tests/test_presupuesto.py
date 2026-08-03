from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import ConsumoFuente
from app.presupuesto import PresupuestoAgotadoError, PresupuestoMensual, SinLimite


def reloj(fecha: datetime):
    return lambda: fecha


AGOSTO = datetime(2026, 8, 3, 7, 0)
SEPTIEMBRE = datetime(2026, 9, 1, 7, 0)


def test_consume_hasta_el_limite(sesion):
    p = PresupuestoMensual(sesion, "jsearch", limite=3, reloj=reloj(AGOSTO))

    assert [p.intenta_consumir() for _ in range(3)] == [True, True, True]
    assert p.restante() == 0


def test_al_llegar_al_limite_deja_de_permitir_y_no_incrementa(sesion):
    p = PresupuestoMensual(sesion, "jsearch", limite=2, reloj=reloj(AGOSTO))
    p.intenta_consumir()
    p.intenta_consumir()

    assert p.intenta_consumir() is False
    assert p.restante() == 0

    fila = sesion.scalar(select(ConsumoFuente))
    assert fila.peticiones == 2


def test_el_consumo_persiste_entre_instancias(sesion):
    PresupuestoMensual(sesion, "jsearch", limite=10, reloj=reloj(AGOSTO)).intenta_consumir(4)

    otra = PresupuestoMensual(sesion, "jsearch", limite=10, reloj=reloj(AGOSTO))

    assert otra.restante() == 6


def test_el_cupo_se_reinicia_al_cambiar_de_mes(sesion):
    PresupuestoMensual(sesion, "jsearch", limite=5, reloj=reloj(AGOSTO)).intenta_consumir(5)

    septiembre = PresupuestoMensual(sesion, "jsearch", limite=5, reloj=reloj(SEPTIEMBRE))

    assert septiembre.restante() == 5
    assert septiembre.intenta_consumir() is True


def test_cada_fuente_lleva_su_propio_cupo(sesion):
    PresupuestoMensual(sesion, "jsearch", limite=5, reloj=reloj(AGOSTO)).intenta_consumir(5)

    otra_fuente = PresupuestoMensual(sesion, "otra", limite=5, reloj=reloj(AGOSTO))

    assert otra_fuente.restante() == 5


def test_no_se_permite_un_consumo_que_pasaria_del_limite(sesion):
    """Pedir 3 con 2 disponibles no consume 2 y falla: no consume nada."""
    p = PresupuestoMensual(sesion, "jsearch", limite=2, reloj=reloj(AGOSTO))

    assert p.intenta_consumir(3) is False
    assert p.restante() == 2


def test_exige_o_lanza_cuando_no_queda_cupo(sesion):
    p = PresupuestoMensual(sesion, "jsearch", limite=1, reloj=reloj(AGOSTO))
    p.exige()

    with pytest.raises(PresupuestoAgotadoError, match="jsearch"):
        p.exige()


def test_sin_limite_siempre_permite():
    p = SinLimite()

    assert p.intenta_consumir(1000) is True
    assert p.restante() == float("inf")
    p.exige()
