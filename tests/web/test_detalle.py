"""Detalle de una oferta y reclasificar.

El detalle es donde se comprueba de qué se decidió: descripción completa,
razonamiento, los cinco ejes, skills que faltan, red flags, y con qué modelo y
versión de prompt se juzgó.

Ningún test llama a un modelo de verdad: el proveedor se sustituye por
`FakeProvider`, incluido el caso de fallo.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.classify import PROMPT_VERSION
from app.decisiones import ESTADO_GUARDADA, registra_decision
from app.llm.fake import FakeProvider
from app.models import Clasificacion
from app.schemas import EjesEncaje, ResultadoClasificacion
from app.web import routes_ofertas

EJES = {
    "tecnico": "Python coincide con tu experiencia",
    "seniority": "Senior, encaja",
    "modalidad": "Remoto total",
    "salario": "no publicado",
    "sector": "software",
}


@pytest.fixture
def crea_clasificada(sesion, crea_oferta):
    def _crea(
        *,
        categoria: str = "revisar",
        confianza: str = "alta",
        razonamiento: str = "Encaja en lo técnico pero no publican salario.",
        skills_faltantes: list[str] | None = None,
        red_flags: list[str] | None = None,
        modelo: str = "modelo-de-prueba",
        prompt_version: int = 1,
        **campos,
    ):
        oferta = crea_oferta(estado_clasificacion="clasificada", **campos)
        sesion.add(
            Clasificacion(
                job_id=oferta.id,
                categoria=categoria,
                confianza=confianza,
                razonamiento=razonamiento,
                ejes=EJES,
                skills_faltantes=skills_faltantes or ["Kubernetes"],
                red_flags=red_flags or ["No publican salario"],
                modelo=modelo,
                prompt_version=prompt_version,
            )
        )
        sesion.commit()
        return oferta

    return _crea


def resultado(categoria: str = "aplicar_ya", razonamiento: str = "Ahora sí encaja.") -> ResultadoClasificacion:
    return ResultadoClasificacion(
        categoria=categoria,
        confianza="alta",
        razonamiento=razonamiento,
        ejes=EjesEncaje(
            tecnico="Python", seniority="Senior", modalidad="Remoto",
            salario="no publicado", sector="software", zona="Barcelona"
        ),
        skills_faltantes=["Terraform"],
        red_flags=[],
    )


def usa_proveedor(cliente: TestClient, provider) -> None:
    """Sustituye el proveedor del clasificador para esta aplicación de prueba."""
    cliente.app.dependency_overrides[routes_ofertas.get_provider] = lambda: provider


def clasificacion_de(sesion, job_id: int) -> Clasificacion:
    return sesion.execute(
        select(Clasificacion).where(Clasificacion.job_id == job_id)
    ).scalar_one()


def test_el_detalle_muestra_razonamiento_ejes_modelo_y_version(
    cliente: TestClient, crea_clasificada
):
    oferta = crea_clasificada()

    respuesta = cliente.get(f"/job/{oferta.id}")

    assert respuesta.status_code == 200
    html = respuesta.text
    assert "Encaja en lo técnico pero no publican salario." in html
    for eje in EJES.values():
        assert eje in html
    assert "Kubernetes" in html
    assert "No publican salario" in html
    assert "modelo-de-prueba" in html
    assert re.search(r"[Vv]ersión de prompt[^0-9]*1", html)


def test_el_detalle_muestra_la_descripcion_completa(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada(descripcion="Buscamos backend con Python y PostgreSQL.")

    html = cliente.get(f"/job/{oferta.id}").text

    assert "Buscamos backend con Python y PostgreSQL." in html


def test_el_detalle_avisa_de_que_la_descripcion_esta_cortada(
    cliente: TestClient, crea_clasificada
):
    """No basta con marcarlo en el listado: aquí es donde se lee el texto."""
    oferta = crea_clasificada(descripcion_truncada=True)

    html = cliente.get(f"/job/{oferta.id}").text

    assert "fragmento" in html
    assert "sólo publicó" in html


def test_el_detalle_de_una_oferta_completa_no_avisa_de_recorte(
    cliente: TestClient, crea_clasificada
):
    oferta = crea_clasificada(descripcion_truncada=False, descripcion="Texto completo.")

    html = cliente.get(f"/job/{oferta.id}").text

    assert "Texto completo." in html
    assert "fragmento" not in html


def test_el_detalle_enlaza_al_original_en_pestana_nueva(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada(url="https://ofertas.ejemplo/detalle")

    html = cliente.get(f"/job/{oferta.id}").text

    assert 'href="https://ofertas.ejemplo/detalle"' in html
    assert 'target="_blank"' in html
    assert "noopener" in html


def test_desde_el_detalle_tambien_se_decide(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada()

    html = cliente.get(f"/job/{oferta.id}").text

    assert f'id="decision-{oferta.id}"' in html
    assert 'name="motivo"' in html


def test_el_detalle_muestra_la_decision_ya_tomada(cliente: TestClient, crea_clasificada, sesion):
    oferta = crea_clasificada()
    registra_decision(sesion, oferta.id, ESTADO_GUARDADA, "Me la guardo para el finde.")

    html = cliente.get(f"/job/{oferta.id}").text

    assert "Me la guardo para el finde." in html


def test_una_oferta_inexistente_responde_404(cliente: TestClient, crea_clasificada):
    # Con una oferta de control el 404 no puede venir de que falte la ruta.
    existente = crea_clasificada()

    assert cliente.get(f"/job/{existente.id}").status_code == 200
    assert cliente.get("/job/9999").status_code == 404


def test_reclasificar_sustituye_la_clasificacion_y_actualiza_la_version(
    cliente: TestClient, crea_clasificada, sesion, perfil_y_preferencias
):
    oferta = crea_clasificada(categoria="descartar", prompt_version=1)
    usa_proveedor(cliente, FakeProvider(respuestas=[resultado()]))

    respuesta = cliente.post(f"/job/{oferta.id}/reclasificar")

    assert respuesta.status_code == 200
    sesion.expire_all()
    nueva = clasificacion_de(sesion, oferta.id)
    assert nueva.categoria == "aplicar_ya"
    assert nueva.razonamiento == "Ahora sí encaja."
    assert nueva.prompt_version == PROMPT_VERSION
    assert nueva.modelo == "fake"
    # Sustituye, no acumula: dos clasificaciones para la misma oferta harían que
    # el listado enseñara una categoría distinta a la del detalle.
    assert sesion.scalar(select(func.count()).select_from(Clasificacion)) == 1


def test_reclasificar_muestra_el_resultado_nuevo(
    cliente: TestClient, crea_clasificada, perfil_y_preferencias
):
    oferta = crea_clasificada()
    usa_proveedor(cliente, FakeProvider(respuestas=[resultado(razonamiento="Encaja de sobra.")]))

    html = cliente.post(f"/job/{oferta.id}/reclasificar").text

    assert "Encaja de sobra." in html


def test_reclasificar_usa_las_preferencias_y_el_perfil_vigentes(
    cliente: TestClient, crea_clasificada, perfil_y_preferencias
):
    """El botón sirve para eso: volver a juzgar con lo que el usuario acaba de cambiar."""
    oferta = crea_clasificada()
    proveedor = FakeProvider(respuestas=[resultado()])
    usa_proveedor(cliente, proveedor)

    cliente.post(f"/job/{oferta.id}/reclasificar")

    assert len(proveedor.llamadas) == 1
    enviado = proveedor.llamadas[0]["user"]
    assert "Prefiero equipos pequeños." in enviado
    assert "Desarrollador backend" in enviado


def test_si_el_proveedor_falla_la_clasificacion_anterior_queda_intacta(
    cliente: TestClient, crea_clasificada, sesion, perfil_y_preferencias
):
    oferta = crea_clasificada(categoria="descartar", razonamiento="La de antes.")
    usa_proveedor(
        cliente,
        FakeProvider(respuestas=[resultado()], error=RuntimeError("el proveedor se cayó")),
    )

    respuesta = cliente.post(f"/job/{oferta.id}/reclasificar")

    assert respuesta.status_code == 200
    # El mensaje real, no un "ha ocurrido un error" que no dice nada.
    assert "el proveedor se cayó" in respuesta.text
    assert "La de antes." in respuesta.text
    sesion.expire_all()
    anterior = clasificacion_de(sesion, oferta.id)
    assert anterior.categoria == "descartar"
    assert anterior.razonamiento == "La de antes."


def test_si_no_hay_perfil_reclasificar_lo_dice_en_la_vista(
    cliente: TestClient, crea_clasificada, sesion
):
    """Sin perfil no se puede clasificar; la vista no puede quedarse en blanco."""
    oferta = crea_clasificada()
    usa_proveedor(cliente, FakeProvider(respuestas=[resultado()]))

    respuesta = cliente.post(f"/job/{oferta.id}/reclasificar")

    assert respuesta.status_code == 200
    assert "No hay perfil cargado" in respuesta.text
    sesion.expire_all()
    assert clasificacion_de(sesion, oferta.id).categoria == "revisar"


def test_reclasificar_una_oferta_inexistente_responde_404(
    cliente: TestClient, perfil_y_preferencias
):
    usa_proveedor(cliente, FakeProvider(respuestas=[resultado()]))

    assert cliente.post("/job/9999/reclasificar").status_code == 404


def test_el_detalle_ofrece_el_boton_de_reclasificar(cliente: TestClient, crea_clasificada):
    oferta = crea_clasificada()

    html = cliente.get(f"/job/{oferta.id}").text

    assert f'action="/job/{oferta.id}/reclasificar"' in html
    assert "Reclasificar" in html


def test_la_ficha_pinta_el_eje_de_zona(cliente: TestClient, sesion, crea_clasificada):
    """El eje sirve para que el descarte por ubicación sea auditable, y para eso hay que
    verlo."""
    oferta = crea_clasificada()
    fila = clasificacion_de(sesion, oferta.id)
    fila.ejes = {**EJES, "zona": "Alicante, fuera de las zonas aceptadas"}
    sesion.commit()

    html = cliente.get(f"/job/{oferta.id}").text

    assert "Zona" in html
    assert "Alicante, fuera de las zonas aceptadas" in html


def test_una_clasificacion_sin_eje_de_zona_sigue_pintando(
    cliente: TestClient, crea_clasificada
):
    """La regresión que protege a las 334 clasificaciones ya guardadas.

    Ninguna tiene el eje `zona`, porque se emitieron antes de que existiera. `_ejes()`
    sólo pinta las claves presentes, así que deben seguir mostrando sus cinco filas. Si
    alguien lo cambiara por un acceso directo al campo, esto se pondría rojo.
    """
    oferta = crea_clasificada()

    respuesta = cliente.get(f"/job/{oferta.id}")

    assert respuesta.status_code == 200
    for eje in EJES.values():
        assert eje in respuesta.text
