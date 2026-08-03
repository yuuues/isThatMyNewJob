"""Vista de preferencias: el formulario que más afina al clasificador.

Dos cosas que aquí son requisito y no adorno, y por eso tienen test propio:

- Las **notas** se inyectan tal cual en el prompt. Si la vista no lo explica, se
  quedan vacías para siempre y el sistema clasifica peor de lo que podría.
- Los **vetos** descartan antes de llamar al modelo y sin que el usuario lo vea.
  Medido sobre datos reales: vetar `java` ocultaba 3 ofertas válidas de 197.
"""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Job, PreferenciasRow
from app.schemas import Preferencias

FORMULARIO = {
    "salario_min": "50000",
    "modalidades": ["remoto", "hibrido"],
    "zonas": "Valencia, Madrid",
    "sectores_veto": "apuestas, tabaco",
    "tecnologias_veto": "php, cobol",
    "idiomas": ["es", "en"],
    "notas": "Nada de guardias nocturnas.",
}


def _preferencias(sesion) -> Preferencias:
    fila = sesion.scalar(select(PreferenciasRow).order_by(PreferenciasRow.id.desc()))
    return Preferencias.model_validate(fila.datos)


def test_la_vista_muestra_las_preferencias_actuales(cliente: TestClient, perfil_y_preferencias):
    respuesta = cliente.get("/preferences")

    assert respuesta.status_code == 200
    assert "45000" in respuesta.text
    assert "Prefiero equipos pequeños." in respuesta.text
    assert "apuestas" in respuesta.text


def test_la_vista_responde_sin_preferencias_guardadas(cliente: TestClient):
    respuesta = cliente.get("/preferences")

    assert respuesta.status_code == 200
    assert "<form" in respuesta.text


def test_guardar_persiste_y_se_lee_de_vuelta(cliente: TestClient, sesion, perfil_y_preferencias):
    respuesta = cliente.post("/preferences", data=FORMULARIO)

    assert respuesta.status_code == 200

    prefs = _preferencias(sesion)
    assert prefs.salario_min == 50000
    assert prefs.modalidades == ["remoto", "hibrido"]
    assert prefs.zonas == ["Valencia", "Madrid"]
    assert prefs.sectores_veto == ["apuestas", "tabaco"]
    assert prefs.tecnologias_veto == ["php", "cobol"]
    assert prefs.idiomas == ["es", "en"]
    assert prefs.notas == "Nada de guardias nocturnas."

    texto = cliente.get("/preferences").text
    assert "50000" in texto
    assert "Nada de guardias nocturnas." in texto


def test_guardar_sin_preferencias_previas_crea_la_fila(cliente: TestClient, sesion):
    cliente.post("/preferences", data=FORMULARIO)

    assert _preferencias(sesion).salario_min == 50000


def test_un_salario_no_numerico_se_rechaza_sin_romper(
    cliente: TestClient, sesion, perfil_y_preferencias
):
    respuesta = cliente.post("/preferences", data={**FORMULARIO, "salario_min": "mucho dinero"})

    assert respuesta.status_code == 400
    assert "número" in respuesta.text
    # Y no se ha tocado nada: rechazar es rechazar, no guardar a medias.
    assert _preferencias(sesion).salario_min == 45000


def test_un_salario_vacio_significa_sin_minimo(cliente: TestClient, sesion, perfil_y_preferencias):
    cliente.post("/preferences", data={**FORMULARIO, "salario_min": ""})

    assert _preferencias(sesion).salario_min is None


def test_las_notas_llevan_explicacion_visible(cliente: TestClient):
    """El campo más útil del formulario es el que menos se entiende."""
    texto = cliente.get("/preferences").text.lower()

    assert "notas" in texto
    assert "prompt" in texto


def test_los_vetos_llevan_aviso_visible(cliente: TestClient):
    """Un veto descarta en silencio y sin gastar llamada al modelo. Hay que decirlo."""
    texto = cliente.get("/preferences").text.lower()

    assert "veto" in texto
    assert "sin llegar al modelo" in texto or "sin llamar al modelo" in texto
    # El caso medido, para que el aviso no sea una vaguedad.
    assert "197" in texto


def test_guardar_ofrece_reevaluar_el_prefiltro(cliente: TestClient, perfil_y_preferencias):
    respuesta = cliente.post("/preferences", data=FORMULARIO)

    assert "/preferences/reevaluar" in respuesta.text


def test_reevaluar_devuelve_a_pendiente_lo_descartado_por_una_regla_que_ya_no_aplica(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    oferta = crea_oferta(
        descripcion="Puesto de backend en Python.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )

    respuesta = cliente.post("/preferences/reevaluar")

    assert respuesta.status_code == 200
    sesion.refresh(oferta)
    assert oferta.estado_clasificacion == "pendiente"
    assert oferta.motivo_regla is None


def test_reevaluar_mantiene_descartado_lo_que_sigue_vetado(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    oferta = crea_oferta(
        descripcion="Mantenimiento de una aplicación PHP heredada.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )

    cliente.post("/preferences/reevaluar")

    sesion.refresh(oferta)
    assert oferta.estado_clasificacion == "descartada_por_regla"
    assert oferta.motivo_regla == "palabra vetada: php"


def test_reevaluar_no_toca_las_ofertas_ya_clasificadas(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    # La descripción no incumple ninguna regla a propósito: si el reevaluador mirase
    # estados que no le tocan, esta oferta acabaría en `pendiente` y volvería a costar
    # una llamada al modelo que ya se pagó.
    clasificada = crea_oferta(descripcion="Backend en Python.", estado_clasificacion="clasificada")
    vetada = crea_oferta(
        descripcion="Mantenimiento de una aplicación PHP heredada.",
        estado_clasificacion="clasificada",
    )

    cliente.post("/preferences/reevaluar")

    sesion.refresh(clasificada)
    sesion.refresh(vetada)
    assert clasificada.estado_clasificacion == "clasificada"
    assert vetada.estado_clasificacion == "clasificada"
    assert vetada.motivo_regla is None


def test_reevaluar_informa_de_cuantas_ha_devuelto(
    cliente: TestClient, crea_oferta, perfil_y_preferencias
):
    crea_oferta(
        descripcion="Backend en Python.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )
    crea_oferta(
        descripcion="Backend en Go.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )

    respuesta = cliente.post("/preferences/reevaluar")

    assert "2 ofertas vuelven a la cola" in respuesta.text


def test_quitar_un_veto_y_reevaluar_recupera_la_oferta(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    """El circuito completo que motiva la tarea: veto retirado, oferta de vuelta."""
    oferta = crea_oferta(
        descripcion="Mantenimiento de una aplicación PHP heredada.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )

    cliente.post("/preferences", data={**FORMULARIO, "tecnologias_veto": ""})
    cliente.post("/preferences/reevaluar")

    sesion.refresh(oferta)
    assert oferta.estado_clasificacion == "pendiente"


def test_las_ofertas_en_estado_error_no_se_reevaluan(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    """`error` es terminal por agotar intentos; el prefiltro no tiene nada que decir."""
    oferta = crea_oferta(descripcion="Backend en Python.", estado_clasificacion="error")

    cliente.post("/preferences/reevaluar")

    sesion.refresh(oferta)
    assert oferta.estado_clasificacion == "error"


def test_reevaluar_sin_nada_descartado_no_rompe(cliente: TestClient, perfil_y_preferencias):
    assert cliente.post("/preferences/reevaluar").status_code == 200


def test_la_oferta_reencolada_vuelve_a_estar_en_la_cola(
    cliente: TestClient, sesion, crea_oferta, perfil_y_preferencias
):
    crea_oferta(
        descripcion="Backend en Python.",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="palabra vetada: php",
    )

    cliente.post("/preferences/reevaluar")

    pendientes = sesion.scalars(
        select(Job).where(Job.estado_clasificacion == "pendiente")
    ).all()
    assert len(pendientes) == 1
