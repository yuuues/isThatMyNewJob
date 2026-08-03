from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.decisiones import (
    ESTADOS,
    ESTADOS_IGNORADOS,
    ESTADOS_NEGATIVOS,
    ESTADOS_POSITIVOS,
    EstadoDesconocido,
    OfertaNoEncontrada,
    historial_de,
    historial_por_empresa,
    registra_decision,
    resumen_candidaturas,
    signo_estado,
)
from app.models import Decision, Job


def crea_job(sesion, sufijo: str, empresa: str = "Acme S.L.", titulo: str = "Backend") -> Job:
    job = Job(
        fuente="test",
        external_id=sufijo,
        url=f"https://example.com/{sufijo}",
        titulo=titulo,
        empresa=empresa,
        descripcion="descripción de la oferta",
        hash_dedup=f"hash{sufijo}",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.flush()
    return job


def cuenta_decisiones(sesion) -> int:
    return sesion.execute(select(func.count()).select_from(Decision)).scalar_one()


# --- Vocabulario de estados -------------------------------------------------


def test_los_cinco_estados_estan_declarados():
    assert set(ESTADOS) == {
        "guardada",
        "aplicada",
        "en_proceso",
        "rechazado_por_ellos",
        "descartada_por_mi",
    }


def test_el_unico_estado_negativo_es_descartada_por_mi():
    """Blindaje del punto crítico del diseño.

    Si alguien mete `rechazado_por_ellos` entre los negativos, el clasificador
    aprende a esconder justo las ofertas que mejor encajaban y el fallo es
    invisible: simplemente dejan de aparecer buenas ofertas.
    """
    assert ESTADOS_NEGATIVOS == frozenset({"descartada_por_mi"})
    assert "rechazado_por_ellos" not in ESTADOS_NEGATIVOS


def test_rechazado_por_ellos_no_es_positivo_ni_negativo():
    assert signo_estado("rechazado_por_ellos") == "ignorado"
    assert "rechazado_por_ellos" not in ESTADOS_POSITIVOS
    assert "rechazado_por_ellos" not in ESTADOS_NEGATIVOS
    assert "rechazado_por_ellos" in ESTADOS_IGNORADOS


def test_los_positivos_son_guardada_aplicada_y_en_proceso():
    assert ESTADOS_POSITIVOS == frozenset({"guardada", "aplicada", "en_proceso"})
    for estado in ("guardada", "aplicada", "en_proceso"):
        assert signo_estado(estado) == "positivo"


def test_los_tres_grupos_son_disjuntos_y_cubren_todos_los_estados():
    assert ESTADOS_POSITIVOS & ESTADOS_NEGATIVOS == frozenset()
    assert ESTADOS_POSITIVOS & ESTADOS_IGNORADOS == frozenset()
    assert ESTADOS_NEGATIVOS & ESTADOS_IGNORADOS == frozenset()
    assert ESTADOS_POSITIVOS | ESTADOS_NEGATIVOS | ESTADOS_IGNORADOS == set(ESTADOS)


def test_un_estado_desconocido_se_ignora_en_vez_de_contar_como_negativo():
    """El fallo seguro es ignorar: un estado nuevo que nadie clasificó no puede
    colarse como ejemplo negativo por omisión."""
    assert signo_estado("estado_que_no_existe") == "ignorado"


# --- Persistencia de decisiones ---------------------------------------------


@pytest.mark.parametrize("estado", ESTADOS)
def test_cada_estado_se_persiste_y_se_lee_de_vuelta(sesion, estado):
    job = crea_job(sesion, estado)

    registra_decision(sesion, job.id, estado, motivo=f"porque {estado}")

    guardada = sesion.get(Decision, 1)
    assert guardada.estado == estado
    assert guardada.motivo == f"porque {estado}"
    assert guardada.job_id == job.id
    assert guardada.actualizada_en is not None


def test_un_estado_invalido_no_se_guarda(sesion):
    job = crea_job(sesion, "1")

    with pytest.raises(EstadoDesconocido):
        registra_decision(sesion, job.id, "interesa", motivo="estado viejo")

    assert cuenta_decisiones(sesion) == 0


def test_decidir_sobre_una_oferta_inexistente_no_crea_nada(sesion):
    with pytest.raises(OfertaNoEncontrada):
        registra_decision(sesion, 12345, "guardada", motivo="no existe")

    assert cuenta_decisiones(sesion) == 0


def test_decidir_dos_veces_actualiza_la_misma_fila(sesion):
    job = crea_job(sesion, "1")

    registra_decision(sesion, job.id, "guardada", motivo="me interesa")
    registra_decision(sesion, job.id, "aplicada", motivo="me presenté")

    assert cuenta_decisiones(sesion) == 1
    assert sesion.get(Decision, 1).estado == "aplicada"


def test_el_motivo_puede_quedarse_vacio(sesion):
    job = crea_job(sesion, "1")

    decision = registra_decision(sesion, job.id, "guardada")

    assert decision.motivo == ""


# --- aplicada_en ------------------------------------------------------------


def test_aplicada_fija_aplicada_en(sesion):
    job = crea_job(sesion, "1")

    decision = registra_decision(sesion, job.id, "aplicada", motivo="me presenté")

    assert decision.aplicada_en is not None


def test_un_estado_que_no_es_aplicada_no_fija_aplicada_en(sesion):
    job = crea_job(sesion, "1")

    decision = registra_decision(sesion, job.id, "guardada", motivo="para luego")

    assert decision.aplicada_en is None


def test_aplicada_en_no_se_mueve_al_avanzar_el_proceso(sesion):
    """La fecha sirve para contar a cuántas ofertas te presentaste en un mes.
    Si se moviera con cada cambio de estado, el recuento mentiría."""
    job = crea_job(sesion, "1")
    registra_decision(sesion, job.id, "aplicada", motivo="me presenté")
    primera = sesion.get(Decision, 1).aplicada_en

    registra_decision(sesion, job.id, "en_proceso", motivo="me llamaron")
    assert sesion.get(Decision, 1).aplicada_en == primera

    registra_decision(sesion, job.id, "rechazado_por_ellos", motivo="no siguieron")
    assert sesion.get(Decision, 1).aplicada_en == primera


def test_actualizada_en_avanza_al_volver_a_decidir(sesion):
    job = crea_job(sesion, "1")
    registra_decision(sesion, job.id, "guardada", motivo="para luego")
    sesion.get(Decision, 1).actualizada_en = datetime(2026, 1, 1)
    sesion.commit()

    registra_decision(sesion, job.id, "aplicada", motivo="me presenté")

    assert sesion.get(Decision, 1).actualizada_en > datetime(2026, 1, 1)


# --- Memoria por empresa ----------------------------------------------------


def test_la_memoria_agrupa_las_variantes_del_nombre_de_la_empresa(sesion):
    vieja = crea_job(sesion, "1", empresa="Acme S.L.", titulo="Backend")
    nueva = crea_job(sesion, "2", empresa="ACME SL", titulo="Backend Senior")
    registra_decision(sesion, vieja.id, "aplicada", motivo="me presenté")

    historial = historial_por_empresa(sesion, [nueva])

    assert list(historial) == ["acme"]
    assert len(historial["acme"]) == 1
    assert historial["acme"][0].job_id == vieja.id


def test_una_empresa_sin_historial_devuelve_vacio(sesion):
    job = crea_job(sesion, "1", empresa="Sin Historia SL")

    historial = historial_por_empresa(sesion, [job])

    assert historial["sin historia"] == []


def test_sin_ofertas_la_memoria_esta_vacia(sesion):
    assert historial_por_empresa(sesion, []) == {}


def test_el_historial_dice_estado_fecha_y_a_que_oferta_correspondia(sesion):
    job = crea_job(sesion, "1", empresa="Acme S.L.", titulo="Backend")
    otra = crea_job(sesion, "2", empresa="Acme S.L.", titulo="Frontend")
    registra_decision(sesion, job.id, "rechazado_por_ellos", motivo="no siguieron")

    entrada = historial_por_empresa(sesion, [otra])["acme"][0]

    assert entrada.job_id == job.id
    assert entrada.titulo == "Backend"
    assert entrada.empresa == "Acme S.L."
    assert entrada.estado == "rechazado_por_ellos"
    assert entrada.motivo == "no siguieron"
    assert entrada.decidida_en is not None
    assert entrada.aplicada_en is None


def test_el_historial_va_de_la_decision_mas_reciente_a_la_mas_antigua(sesion):
    antigua = crea_job(sesion, "1", empresa="Acme", titulo="Antigua")
    reciente = crea_job(sesion, "2", empresa="Acme", titulo="Reciente")
    registra_decision(sesion, antigua.id, "descartada_por_mi", motivo="no")
    registra_decision(sesion, reciente.id, "guardada", motivo="sí")
    sesion.get(Decision, 1).actualizada_en = datetime(2026, 1, 1)
    sesion.get(Decision, 2).actualizada_en = datetime(2026, 7, 1)
    sesion.commit()

    titulos = [e.titulo for e in historial_por_empresa(sesion, [antigua, reciente])["acme"]]

    assert titulos == ["Reciente", "Antigua"]


def test_las_ofertas_sin_decidir_no_dejan_rastro_en_el_historial(sesion):
    job = crea_job(sesion, "1", empresa="Acme")
    crea_job(sesion, "2", empresa="Acme")

    assert historial_por_empresa(sesion, [job])["acme"] == []


def test_el_historial_de_una_oferta_excluye_su_propia_decision(sesion):
    """La fila de la oferta ya muestra su decisión; repetirla como 'historial
    previo' haría creer que hay dos candidaturas donde sólo hay una."""
    job = crea_job(sesion, "1", empresa="Acme", titulo="Backend")
    hermana = crea_job(sesion, "2", empresa="Acme", titulo="Frontend")
    registra_decision(sesion, job.id, "aplicada", motivo="me presenté")
    registra_decision(sesion, hermana.id, "descartada_por_mi", motivo="no me gusta")

    historial = historial_por_empresa(sesion, [job, hermana])

    assert [e.titulo for e in historial_de(historial, job)] == ["Frontend"]
    assert [e.titulo for e in historial_de(historial, hermana)] == ["Backend"]


def test_el_historial_de_una_oferta_de_empresa_desconocida_no_revienta(sesion):
    job = crea_job(sesion, "1", empresa="Acme")

    assert historial_de({}, job) == []


# --- Recuento de candidaturas -----------------------------------------------


def test_cuenta_las_aplicadas_del_periodo_y_las_que_siguen_en_proceso(sesion):
    for i, estado in enumerate(("aplicada", "aplicada", "en_proceso", "descartada_por_mi")):
        job = crea_job(sesion, f"j{i}", empresa=f"Empresa {i}")
        registra_decision(sesion, job.id, estado, motivo="motivo")
    # `en_proceso` también pasó por `aplicada`: presentarse es un proceso.
    sesion.get(Decision, 3).aplicada_en = datetime(2026, 8, 2)
    for fila in (1, 2):
        sesion.get(Decision, fila).aplicada_en = datetime(2026, 8, 10)
    sesion.commit()

    resumen = resumen_candidaturas(sesion, periodo="2026-08")

    assert resumen.aplicadas == 3
    assert resumen.en_proceso == 1


def test_un_periodo_sin_candidaturas_devuelve_ceros(sesion):
    job = crea_job(sesion, "1")
    registra_decision(sesion, job.id, "aplicada", motivo="me presenté")
    sesion.get(Decision, 1).aplicada_en = datetime(2026, 1, 5)
    sesion.commit()

    resumen = resumen_candidaturas(sesion, periodo="2026-08")

    assert resumen.aplicadas == 0
    assert resumen.en_proceso == 0
