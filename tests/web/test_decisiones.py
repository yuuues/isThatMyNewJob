"""Decidir sobre una oferta desde la interfaz.

Aquí está el mecanismo de aprendizaje del sistema: `feedback.py` sólo usa las
decisiones CON motivo escrito y descarta las demás, así que el último test de este
fichero cierra el circuito completo (decidir en la web → ejemplo en el prompt).

Ningún test toca `data/app.db` ni llama a ningún modelo.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.decisiones import (
    ESTADO_APLICADA,
    ESTADO_DESCARTADA_POR_MI,
    ESTADO_EN_PROCESO,
    ESTADO_GUARDADA,
    ESTADO_RECHAZADO_POR_ELLOS,
    ESTADOS,
)
from app.feedback import ejemplos_few_shot
from app.models import Clasificacion, Decision

EJES = {
    "tecnico": "Python coincide",
    "seniority": "Senior, encaja",
    "modalidad": "Remoto",
    "salario": "no publicado",
    "sector": "software",
}


@pytest.fixture
def crea_clasificada(sesion, crea_oferta):
    def _crea(*, categoria: str = "revisar", confianza: str = "alta", **campos):
        oferta = crea_oferta(estado_clasificacion="clasificada", **campos)
        sesion.add(
            Clasificacion(
                job_id=oferta.id,
                categoria=categoria,
                confianza=confianza,
                razonamiento="Encaja con lo que buscas.",
                ejes=EJES,
                skills_faltantes=[],
                red_flags=[],
                modelo="fake",
                prompt_version=2,
            )
        )
        sesion.commit()
        return oferta

    return _crea


def decide(cliente: TestClient, job_id: int, estado: str, motivo: str = ""):
    return cliente.post(
        f"/job/{job_id}/decision",
        data={"estado": estado, "motivo": motivo},
        headers={"HX-Request": "true"},
    )


def decision_de(sesion, job_id: int) -> Decision | None:
    return sesion.execute(select(Decision).where(Decision.job_id == job_id)).scalar_one_or_none()


@pytest.mark.parametrize("estado", ESTADOS)
def test_cada_estado_se_guarda_con_su_motivo(cliente: TestClient, crea_clasificada, sesion, estado):
    oferta = crea_clasificada()

    respuesta = decide(cliente, oferta.id, estado, f"Motivo de {estado}.")

    assert respuesta.status_code == 200
    guardada = decision_de(sesion, oferta.id)
    assert guardada is not None
    assert guardada.estado == estado
    assert guardada.motivo == f"Motivo de {estado}."


def test_marcar_aplicada_fija_la_fecha_de_candidatura(
    cliente: TestClient, crea_clasificada, sesion
):
    oferta = crea_clasificada()

    decide(cliente, oferta.id, ESTADO_APLICADA, "Me presenté.")

    assert decision_de(sesion, oferta.id).aplicada_en is not None


def test_la_fecha_de_candidatura_no_se_mueve_al_cambiar_de_estado(
    cliente: TestClient, crea_clasificada, sesion
):
    """Si se moviera, el recuento del mes mentiría en cuanto la empresa contestara."""
    oferta = crea_clasificada()
    decide(cliente, oferta.id, ESTADO_APLICADA, "Me presenté.")
    aplicada_en = decision_de(sesion, oferta.id).aplicada_en

    decide(cliente, oferta.id, ESTADO_RECHAZADO_POR_ELLOS, "No siguieron.")

    assert decision_de(sesion, oferta.id).aplicada_en == aplicada_en


def test_decidir_dos_veces_actualiza_en_vez_de_romper(
    cliente: TestClient, crea_clasificada, sesion
):
    """`decision.job_id` es único: un INSERT ciego reventaría en el segundo clic."""
    oferta = crea_clasificada()

    decide(cliente, oferta.id, ESTADO_GUARDADA, "Me la guardo.")
    segunda = decide(cliente, oferta.id, ESTADO_DESCARTADA_POR_MI, "Al final no.")

    assert segunda.status_code == 200
    assert sesion.scalar(select(func.count()).select_from(Decision)) == 1
    assert decision_de(sesion, oferta.id).estado == ESTADO_DESCARTADA_POR_MI
    assert decision_de(sesion, oferta.id).motivo == "Al final no."


def test_una_decision_puede_guardarse_sin_motivo(cliente: TestClient, crea_clasificada, sesion):
    oferta = crea_clasificada()

    respuesta = decide(cliente, oferta.id, ESTADO_GUARDADA, "")

    assert respuesta.status_code == 200
    assert decision_de(sesion, oferta.id).motivo == ""


def test_decidir_sobre_una_oferta_inexistente_responde_404_y_no_crea_nada(
    cliente: TestClient, crea_clasificada, sesion
):
    # Una oferta real de control: así el 404 sólo puede venir de la que no existe,
    # y no de que la ruta entera falte.
    existente = crea_clasificada()
    assert decide(cliente, existente.id, ESTADO_GUARDADA, "Sí existe.").status_code == 200

    respuesta = decide(cliente, 9999, ESTADO_GUARDADA, "No debería guardarse.")

    assert respuesta.status_code == 404
    assert sesion.scalar(select(func.count()).select_from(Decision)) == 1


def test_un_estado_desconocido_se_rechaza_y_no_crea_nada(
    cliente: TestClient, crea_clasificada, sesion
):
    oferta = crea_clasificada()

    respuesta = decide(cliente, oferta.id, "me_lo_pienso", "Estado inventado.")

    assert respuesta.status_code == 400
    assert sesion.scalar(select(func.count()).select_from(Decision)) == 0


def test_tras_decidir_solo_vuelve_el_trozo_de_esa_fila(cliente: TestClient, crea_clasificada):
    """HTMX reemplaza sólo lo decidido: ni la página entera ni la lista completa."""
    oferta = crea_clasificada(titulo="La decidida")
    crea_clasificada(titulo="La de al lado")

    respuesta = decide(cliente, oferta.id, ESTADO_APLICADA, "Me presenté.")

    assert "<nav" not in respuesta.text
    assert "<html" not in respuesta.text
    assert "La de al lado" not in respuesta.text
    assert f'id="decision-{oferta.id}"' in respuesta.text


def test_la_respuesta_muestra_el_estado_recien_guardado(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada()

    respuesta = decide(cliente, oferta.id, ESTADO_EN_PROCESO, "Primera llamada hecha.")

    assert "Hay conversación en marcha" in respuesta.text
    assert "Primera llamada hecha." in respuesta.text


def test_el_formulario_pide_el_motivo_en_el_mismo_gesto(cliente: TestClient, crea_clasificada):
    """El motivo no puede quedar detrás de un segundo paso escondido."""
    crea_clasificada()

    html = cliente.get("/").text

    assert 'name="estado"' in html
    assert 'name="motivo"' in html


def test_la_interfaz_avisa_de_que_sin_motivo_no_se_aprende(cliente: TestClient, crea_clasificada):
    crea_clasificada()

    html = cliente.get("/").text

    assert "no enseña nada al clasificador" in html


def test_la_interfaz_avisa_de_que_el_rechazo_de_la_empresa_no_ensena_nada(
    cliente: TestClient, crea_clasificada
):
    """Es contraintuitivo y hay que decirlo justo donde se decide.

    Un rechazo de la empresa no dice qué quiere el candidato: contarlo como
    ejemplo negativo escondería justo las ofertas que mejor encajan.
    """
    crea_clasificada()

    html = cliente.get("/").text

    assert "La empresa me ha descartado" in html
    assert "no enseña al clasificador a evitar ofertas parecidas" in html


def test_una_decision_con_motivo_llega_al_few_shot_y_una_sin_motivo_no(
    cliente: TestClient, crea_clasificada, sesion
):
    """El circuito completo: decidir en la web es lo que hace que el sistema aprenda."""
    con_motivo = crea_clasificada(titulo="Con motivo escrito")
    sin_motivo = crea_clasificada(titulo="Sin motivo escrito")

    decide(cliente, con_motivo.id, ESTADO_DESCARTADA_POR_MI, "Exigen presencial en Madrid.")
    decide(cliente, sin_motivo.id, ESTADO_DESCARTADA_POR_MI, "")

    ejemplos = ejemplos_few_shot(sesion)

    assert [e.titulo for e in ejemplos] == ["Con motivo escrito"]
    assert ejemplos[0].motivo == "Exigen presencial en Madrid."


def test_un_rechazo_de_la_empresa_no_llega_al_few_shot(
    cliente: TestClient, crea_clasificada, sesion
):
    oferta = crea_clasificada(titulo="Me rechazaron")

    respuesta = decide(cliente, oferta.id, ESTADO_RECHAZADO_POR_ELLOS, "No siguieron adelante.")

    # La decisión se guarda; lo que no ocurre es que se convierta en ejemplo.
    assert respuesta.status_code == 200
    assert decision_de(sesion, oferta.id).estado == ESTADO_RECHAZADO_POR_ELLOS
    assert ejemplos_few_shot(sesion) == []


# --- Ofertas cerradas ---------------------------------------------------


def test_marcar_cerrada_no_borra_la_decision(cliente: TestClient, sesion, crea_oferta):
    """El caso real: te presentas y luego retiran el puesto. Las dos cosas son ciertas."""
    job = crea_oferta()
    cliente.post(f"/job/{job.id}/decision", data={"estado": "aplicada", "motivo": "me presenté"})

    respuesta = cliente.post(f"/job/{job.id}/cerrada")

    assert respuesta.status_code == 200
    sesion.refresh(job)
    assert job.cerrada is True
    assert job.decision.estado == "aplicada"


def test_reabrir_deshace_la_marca(cliente: TestClient, sesion, crea_oferta):
    job = crea_oferta()
    cliente.post(f"/job/{job.id}/cerrada")

    cliente.post(f"/job/{job.id}/cerrada", data={"abierta": "si"})

    sesion.refresh(job)
    assert job.cerrada is False


def test_cerrar_una_oferta_inexistente_responde_404(cliente: TestClient):
    assert cliente.post("/job/999/cerrada").status_code == 404


def test_las_cerradas_desaparecen_del_listado(cliente: TestClient, sesion, crea_clasificada):
    """El puesto ya no existe: revisarlo es tiempo perdido."""
    viva = crea_clasificada(titulo="Sigue abierta")
    muerta = crea_clasificada(titulo="Ya cerrada")
    cliente.post(f"/job/{muerta.id}/cerrada")

    texto = cliente.get("/").text

    assert viva.titulo in texto
    assert muerta.titulo not in texto


def test_el_filtro_las_recupera(cliente: TestClient, sesion, crea_clasificada):
    muerta = crea_clasificada(titulo="Ya cerrada")
    cliente.post(f"/job/{muerta.id}/cerrada")

    assert muerta.titulo in cliente.get("/?cerradas=si").text
