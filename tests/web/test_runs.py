"""Vista de diagnóstico: histórico de runs, descartes por regla y cupo de JSearch.

Estos tests valen más de lo que parece. La lista de descartes por regla es la que
hizo aflorar tres defectos reales del sistema (ofertas de ámbito nacional
descartadas por un filtro de ciudades, tarifas por hora comparadas contra un
mínimo anual, y una fuente entera marcada como presencial por defecto). Un veto
mal puesto no da error: simplemente deja de traer ofertas, y sin esta vista el
fallo es invisible durante semanas.

Nada de aquí toca `data/app.db` ni la red: la sesión es la de memoria del
`conftest` de web y el cupo se lee de `source_usage`, no del proveedor.
"""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import ConsumoFuente, Run
from app.presupuesto import PresupuestoMensual
from app.web import routes_runs

# Las únicas clases CSS que existen en `estilo.css`, más el `container` de Pico.
# La plantilla no puede inventar otras: Pico es un framework SIN clases y una clase
# inventada no recibe ningún estilo, así que el ajuste se vería bien en el navegador
# de quien lo escribió y en ningún otro sitio.
CLASES_PERMITIDAS = {"container", "tarjeta", "tenue", "aviso", "etiqueta"}


def _mes_en_curso() -> str:
    """El formato de periodo que escribe `PresupuestoMensual`, repetido a propósito.

    No se reutiliza `routes_runs.periodo_actual()`: si el test leyera con la misma
    función que la vista, un cambio de formato pasaría los tests y dejaría de
    encontrar la fila real de `source_usage`.
    """
    return datetime.now(UTC).strftime("%Y-%m")


def _texto(html: str) -> str:
    """Texto visible, sin etiquetas y con los espacios normalizados."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _resumenes(html: str) -> list[str]:
    """Texto de cada `<summary>`, que es donde van los encabezados de los grupos."""
    return [_texto(s) for s in re.findall(r"<summary[^>]*>(.*?)</summary>", html, re.S)]


def _resumen_con(html: str, aguja: str) -> str:
    coincidencias = [s for s in _resumenes(html) if aguja in s]
    assert coincidencias, f"ningún <summary> menciona {aguja!r}; hay {_resumenes(html)}"
    return coincidencias[0]


def _crea_run(sesion, **campos) -> Run:
    datos = {
        "inicio": datetime(2026, 8, 1, 7, 0),
        "fin": datetime(2026, 8, 1, 7, 12),
        "stats": {},
        "errores": [],
    }
    datos.update(campos)
    run = Run(**datos)
    sesion.add(run)
    sesion.commit()
    return run


@pytest.fixture
def limite_fijo(monkeypatch) -> int:
    """Fija el límite mensual de JSearch para no depender del `.env` de la máquina."""
    limite = 200
    monkeypatch.setattr(
        routes_runs, "get_settings", lambda: Settings(jsearch_limite_mensual=limite)
    )
    return limite


# --- Histórico de runs -------------------------------------------------------


def test_el_historico_muestra_las_cifras_de_cada_fuente(cliente: TestClient, sesion):
    _crea_run(
        sesion,
        stats={
            "adzuna": {"recibidas": 12, "nuevas": 5, "duplicadas": 7},
            "_totales": {"clasificadas": 4, "descartadas_por_regla": 1, "agotadas": 0},
        },
    )

    respuesta = cliente.get("/runs")
    texto = _texto(respuesta.text)

    assert respuesta.status_code == 200
    assert "adzuna" in texto
    for cifra in ("12", "5", "7"):
        assert re.search(rf"\b{cifra}\b", texto), f"no aparece la cifra {cifra}"


def test_la_clave_de_totales_no_se_muestra_como_si_fuera_una_fuente(cliente: TestClient, sesion):
    """`_totales` convive con las fuentes dentro de `stats` pero no es una fuente.

    Si se cuela en la tabla, la vista dice que existe una llamada a un proveedor
    llamado "_totales", que es exactamente el tipo de ruido que hace que nadie mire
    esta pantalla.
    """
    _crea_run(sesion, stats={"adzuna": {"recibidas": 1, "nuevas": 1, "duplicadas": 0}})

    assert "_totales" not in cliente.get("/runs").text


def test_el_historico_muestra_el_inicio_y_el_fin_de_cada_run(cliente: TestClient, sesion):
    run = _crea_run(sesion)

    texto = _texto(cliente.get("/runs").text)

    assert run.inicio.strftime("%d/%m/%Y %H:%M") in texto
    assert run.fin.strftime("%d/%m/%Y %H:%M") in texto


def test_un_run_sin_fin_se_muestra_como_en_curso(cliente: TestClient, sesion):
    """Un run interrumpido deja `fin` a nulo; la vista no puede quedarse en blanco."""
    _crea_run(sesion, fin=None)

    assert "en curso" in _texto(cliente.get("/runs").text).lower()


def test_los_runs_se_ordenan_del_mas_reciente_al_mas_antiguo(cliente: TestClient, sesion):
    _crea_run(sesion, inicio=datetime(2026, 7, 1, 7, 0), stats={"fuenteantigua": {"nuevas": 1}})
    _crea_run(sesion, inicio=datetime(2026, 8, 1, 7, 0), stats={"fuentereciente": {"nuevas": 1}})

    html = cliente.get("/runs").text

    assert html.index("fuentereciente") < html.index("fuenteantigua")


def test_los_errores_del_run_se_muestran_con_tipo_referencia_y_mensaje(
    cliente: TestClient, sesion
):
    _crea_run(
        sesion,
        errores=[
            {
                "tipo": "fuente",
                "fuente": "remotive",
                "job_id": None,
                "error": "TimeoutError: se agotó la espera",
            },
            {
                "tipo": "clasificacion",
                "fuente": "adzuna",
                "job_id": 7,
                "error": "ValueError: respuesta no es JSON",
            },
        ],
    )

    texto = _texto(cliente.get("/runs").text)

    assert "remotive" in texto
    assert "TimeoutError: se agotó la espera" in texto
    assert "clasificacion" in texto
    assert "oferta 7" in texto
    assert "ValueError: respuesta no es JSON" in texto


def test_un_error_incompleto_no_rompe_la_vista(cliente: TestClient, sesion):
    """Los runs antiguos guardaron errores sin `tipo` ni referencia.

    La forma común `{tipo, fuente, job_id, error}` es reciente. Si la vista
    reventara con lo viejo, el histórico dejaría de poder consultarse justo cuando
    hace falta: para mirar hacia atrás.
    """
    _crea_run(sesion, errores=[{"error": "algo salió mal"}])

    respuesta = cliente.get("/runs")

    assert respuesta.status_code == 200
    assert "algo salió mal" in _texto(respuesta.text)


def test_un_run_sin_errores_lo_dice(cliente: TestClient, sesion):
    _crea_run(sesion, stats={"adzuna": {"recibidas": 3, "nuevas": 3, "duplicadas": 0}})

    assert "sin errores" in _texto(cliente.get("/runs").text).lower()


def test_sin_runs_la_vista_responde_y_lo_explica(cliente: TestClient):
    respuesta = cliente.get("/runs")

    assert respuesta.status_code == 200
    assert "todavía no se ha ejecutado" in _texto(respuesta.text).lower()


# --- Descartes por regla -----------------------------------------------------


def test_los_descartes_se_agrupan_por_motivo_y_el_recuento_cuadra(
    cliente: TestClient, crea_oferta
):
    for _ in range(3):
        crea_oferta(estado_clasificacion="descartada_por_regla", motivo_regla="salario bajo")
    crea_oferta(estado_clasificacion="descartada_por_regla", motivo_regla="tecnología vetada: php")

    html = cliente.get("/runs").text

    assert "3" in _resumen_con(html, "salario bajo")
    assert "1" in _resumen_con(html, "tecnología vetada: php")


def test_el_motivo_que_mas_ofertas_se_come_va_primero(cliente: TestClient, crea_oferta):
    """El motivo más numeroso es el que más caro sale si está mal puesto."""
    crea_oferta(estado_clasificacion="descartada_por_regla", motivo_regla="modalidad presencial")
    for _ in range(4):
        crea_oferta(estado_clasificacion="descartada_por_regla", motivo_regla="zona no deseada")

    html = cliente.get("/runs").text

    assert html.index("zona no deseada") < html.index("modalidad presencial")


def test_cada_descarte_enlaza_a_su_oferta(cliente: TestClient, crea_oferta):
    oferta = crea_oferta(
        estado_clasificacion="descartada_por_regla",
        motivo_regla="zona no deseada",
        titulo="Backend en Cuenca",
    )

    html = cliente.get("/runs").text

    assert f'/job/{oferta.id}"' in html
    assert "Backend en Cuenca" in html


def test_un_descarte_sin_motivo_registrado_tambien_aparece(cliente: TestClient, crea_oferta):
    """Sin esto, una oferta descartada sin razón escrita desaparecería para siempre.

    Es la única vista desde la que se puede recuperar, así que agruparla bajo una
    etiqueta fea es preferible a filtrarla.
    """
    crea_oferta(
        estado_clasificacion="descartada_por_regla",
        motivo_regla=None,
        titulo="Oferta sin motivo",
    )

    html = cliente.get("/runs").text

    assert "Oferta sin motivo" in html
    assert _resumenes(html), "no hay ningún grupo de descartes"


def test_las_ofertas_que_no_estan_descartadas_por_regla_no_salen_en_los_descartes(
    cliente: TestClient, crea_oferta
):
    crea_oferta(estado_clasificacion="clasificada", titulo="Oferta clasificada")
    crea_oferta(estado_clasificacion="pendiente", titulo="Oferta pendiente")

    html = cliente.get("/runs").text

    assert "Oferta clasificada" not in html
    assert "Oferta pendiente" not in html


# --- Devolver a la cola ------------------------------------------------------


def test_devolver_una_oferta_a_la_cola_la_deja_pendiente_y_limpia_el_motivo(
    cliente: TestClient, sesion, crea_oferta
):
    oferta = crea_oferta(
        estado_clasificacion="descartada_por_regla", motivo_regla="tecnología vetada: java"
    )

    respuesta = cliente.post(f"/runs/descartes/{oferta.id}/reencolar")
    sesion.refresh(oferta)

    assert respuesta.status_code == 200
    assert oferta.estado_clasificacion == "pendiente"
    # Dejar el motivo puesto la haría reaparecer para siempre en esta misma lista.
    assert oferta.motivo_regla is None


def test_la_oferta_reencolada_desaparece_de_los_descartes(
    cliente: TestClient, crea_oferta
):
    """La respuesta ya trae la lista al día: si siguiera ahí, se reencolaría dos veces."""
    oferta = crea_oferta(
        estado_clasificacion="descartada_por_regla",
        motivo_regla="zona no deseada",
        titulo="Rescatada del veto",
    )

    respuesta = cliente.post(f"/runs/descartes/{oferta.id}/reencolar")

    # Se mira el enlace y no el título: el aviso de confirmación sí nombra la oferta.
    assert f'/job/{oferta.id}"' not in respuesta.text
    assert "zona no deseada" not in respuesta.text


def test_reencolar_una_oferta_inexistente_responde_404(cliente: TestClient):
    assert cliente.post("/runs/descartes/9999/reencolar").status_code == 404


def test_reencolar_una_oferta_ya_clasificada_no_la_toca(
    cliente: TestClient, sesion, crea_oferta
):
    """Reencolar una clasificada le borraría la clasificación en el run siguiente."""
    oferta = crea_oferta(estado_clasificacion="clasificada")

    respuesta = cliente.post(f"/runs/descartes/{oferta.id}/reencolar")
    sesion.refresh(oferta)

    assert respuesta.status_code == 409
    assert oferta.estado_clasificacion == "clasificada"


# --- Ofertas en estado terminal `error` --------------------------------------


def test_las_ofertas_que_agotaron_los_intentos_se_ven_y_se_reintentan(
    cliente: TestClient, sesion, crea_oferta
):
    oferta = crea_oferta(
        estado_clasificacion="error", intentos_clasificacion=3, titulo="Oferta atascada"
    )

    html = cliente.get("/runs").text
    assert "Oferta atascada" in html
    assert f"/runs/errores/{oferta.id}/reintentar" in html

    respuesta = cliente.post(f"/runs/errores/{oferta.id}/reintentar")
    sesion.refresh(oferta)

    assert respuesta.status_code == 200
    assert oferta.estado_clasificacion == "pendiente"
    # Sin poner los intentos a cero el pipeline la ve agotada nada más sacarla de la
    # cola y la devuelve al estado terminal sin llegar a intentarlo.
    assert oferta.intentos_clasificacion == 0


def test_reintentar_una_oferta_inexistente_responde_404(cliente: TestClient):
    assert cliente.post("/runs/errores/9999/reintentar").status_code == 404


def test_reintentar_una_oferta_que_no_esta_en_error_no_la_toca(
    cliente: TestClient, sesion, crea_oferta
):
    oferta = crea_oferta(estado_clasificacion="pendiente", intentos_clasificacion=1)

    respuesta = cliente.post(f"/runs/errores/{oferta.id}/reintentar")
    sesion.refresh(oferta)

    assert respuesta.status_code == 409
    assert oferta.intentos_clasificacion == 1


# --- Cupo de JSearch ---------------------------------------------------------


def test_el_cupo_coincide_con_el_consumo_del_mes_en_curso(
    cliente: TestClient, sesion, limite_fijo
):
    """El consumo lo escribe `PresupuestoMensual`, así que se gasta con él y no a mano.

    Es la mitad del contrato que importa: si la vista leyera `source_usage` con otro
    periodo o con otro nombre de fuente, enseñaría cupo cero para siempre y el
    usuario se enteraría del límite duro cuando JSearch dejara de traer nada.
    """
    assert PresupuestoMensual(sesion, "jsearch", limite=limite_fijo).intenta_consumir(37)

    texto = _texto(cliente.get("/runs").text)

    assert re.search(rf"37\s*de\s*{limite_fijo}", texto), texto
    assert re.search(r"\b163\b", texto), "no se ve cuánto queda"


def test_un_mes_sin_consumo_muestra_el_limite_completo(cliente: TestClient, limite_fijo):
    """La fila de `source_usage` la crea el presupuesto al gastar el primer crédito.

    Que no exista significa cero consumido, no un error ni una vista en blanco.
    """
    respuesta = cliente.get("/runs")
    texto = _texto(respuesta.text)

    assert respuesta.status_code == 200
    assert re.search(rf"0\s*de\s*{limite_fijo}", texto), texto


def test_el_consumo_de_otro_mes_no_cuenta_en_el_actual(cliente: TestClient, sesion, limite_fijo):
    sesion.add(ConsumoFuente(fuente="jsearch", periodo="1999-01", peticiones=150))
    sesion.commit()

    texto = _texto(cliente.get("/runs").text)

    assert re.search(rf"0\s*de\s*{limite_fijo}", texto), texto


def test_el_consumo_de_otra_fuente_no_cuenta_como_cupo_de_jsearch(
    cliente: TestClient, sesion, limite_fijo
):
    sesion.add(ConsumoFuente(fuente="adzuna", periodo=_mes_en_curso(), peticiones=90))
    sesion.commit()

    texto = _texto(cliente.get("/runs").text)

    assert re.search(rf"0\s*de\s*{limite_fijo}", texto), texto


def test_el_periodo_del_cupo_es_el_mes_en_curso(cliente: TestClient, limite_fijo):
    assert _mes_en_curso() in _texto(cliente.get("/runs").text)


def test_periodo_actual_usa_el_formato_de_presupuesto_mensual():
    """`PresupuestoMensual` escribe `%Y-%m`; leer con otro formato daría cero siempre."""
    assert routes_runs.periodo_actual(datetime(2026, 3, 9, tzinfo=UTC)) == "2026-03"


# --- Plantilla ---------------------------------------------------------------


def test_la_vista_hereda_de_la_plantilla_base(cliente: TestClient):
    html = cliente.get("/runs").text

    assert "<html" in html
    assert 'href="/runs"' in html, "falta la navegación de base.html"


def test_la_plantilla_no_inventa_clases_ni_estilos_propios(cliente: TestClient, crea_oferta):
    """Pico estiliza HTML semántico; una clase inventada no recibe ningún estilo."""
    crea_oferta(estado_clasificacion="descartada_por_regla", motivo_regla="zona no deseada")
    html = cliente.get("/runs").text

    assert "<style" not in html
    usadas = {c for atributo in re.findall(r'class="([^"]*)"', html) for c in atributo.split()}
    assert usadas <= CLASES_PERMITIDAS, f"clases desconocidas: {usadas - CLASES_PERMITIDAS}"


def test_la_vista_cuenta_las_ofertas_cerradas_por_fuente(cliente: TestClient, sesion, crea_oferta):
    """Convierte una sensación ("me pasa mucho con adzuna") en un dato con el que
    decidir si conviene retirar una fuente."""
    from app.cerradas import cierra_oferta

    cierra_oferta(sesion, crea_oferta(fuente="adzuna").id)
    cierra_oferta(sesion, crea_oferta(fuente="adzuna").id)
    crea_oferta(fuente="scrappa")

    texto = cliente.get("/runs").text

    assert "Ofertas cerradas por fuente" in texto
    assert "adzuna" in texto
