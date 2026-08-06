from sqlalchemy import select

from app.llm.base import CuotaAgotadaError
from app.llm.fake import FakeProvider
from app.models import Clasificacion, Job, Perfil, PreferenciasRow, Run
from app.pipeline import ESTADO_AGOTADA, MAX_INTENTOS, ejecuta_run
from app.resiliencia import ESPERA_INICIAL, FACTOR_BACKOFF, REINTENTOS
from app.schemas import EjesEncaje, PerfilCandidato, Preferencias, RawJob, ResultadoClasificacion, SearchQuery
from app.sources.fake import FakeSource


class Reloj:
    """Sustituye a time.sleep en el pipeline: anota las esperas sin dormirlas."""

    def __init__(self) -> None:
        self.esperas: list[float] = []

    def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


def prepara(sesion, prefs: Preferencias | None = None) -> None:
    sesion.add(Perfil(datos=PerfilCandidato(anios_experiencia=8, resumen="Backend").model_dump()))
    sesion.add(PreferenciasRow(datos=(prefs or Preferencias()).model_dump()))
    sesion.commit()


def raw(external_id: str, **kwargs) -> RawJob:
    base = dict(
        fuente="fake",
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        titulo="Backend Developer",
        empresa=f"Empresa {external_id}",
        ubicacion="Madrid",
        modalidad="remoto",
        descripcion="We are looking for a backend developer to join the team and build the platform",
    )
    base.update(kwargs)
    return RawJob(**base)


def veredicto(categoria: str = "aplicar_ya") -> ResultadoClasificacion:
    return ResultadoClasificacion(
        categoria=categoria,
        confianza="alta",
        razonamiento="Encaja.",
        ejes=EjesEncaje(
            tecnico="alto", seniority="ok", modalidad="remoto",
            salario="no publicado", sector="ok", zona="dentro de zona"
        ),
    )


def test_el_run_ingesta_clasifica_y_registra(sesion):
    prepara(sesion)
    provider = FakeProvider([veredicto()])

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=provider,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "clasificada"
    assert job.clasificacion.categoria == "aplicar_ya"
    assert job.clasificacion.prompt_version >= 1
    assert run.fin is not None
    assert run.stats["fake"]["nuevas"] == 1


def test_las_ofertas_descartadas_por_regla_no_gastan_llamada_al_llm(sesion):
    prepara(sesion, Preferencias(tecnologias_veto=["cobol"]))
    provider = FakeProvider([veredicto()])

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1", descripcion="Mantenimiento COBOL en banca for the team")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "descartada_por_regla"
    assert "cobol" in job.motivo_regla
    assert provider.llamadas == []


def test_un_fallo_del_llm_deja_la_oferta_pendiente_para_el_siguiente_run(sesion):
    prepara(sesion)
    provider = FakeProvider([], error=RuntimeError("timeout"))

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        dormir=Reloj(),
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "pendiente"
    assert job.intentos_clasificacion == 1
    assert any("timeout" in e["error"] for e in run.errores)


# --- B2: reintentos con backoff ------------------------------------------------


def test_un_fallo_transitorio_se_reintenta_y_clasifica_en_el_mismo_run(sesion):
    prepara(sesion)
    reloj = Reloj()
    provider = FakeProvider([veredicto()], error=RuntimeError("timeout"), fallos=1)

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        dormir=reloj,
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == "clasificada"
    assert job.intentos_clasificacion == 0
    assert len(provider.llamadas) == 2
    assert reloj.esperas == [ESPERA_INICIAL]


def test_los_fallos_seguidos_consumen_los_reintentos_previstos_y_no_mas(sesion):
    prepara(sesion)
    reloj = Reloj()
    provider = FakeProvider([], error=RuntimeError("timeout"))

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        dormir=reloj,
    )

    job = sesion.scalar(select(Job))
    assert len(provider.llamadas) == REINTENTOS + 1
    assert reloj.esperas == [ESPERA_INICIAL, ESPERA_INICIAL * FACTOR_BACKOFF]
    # Los reintentos internos son un solo intento a efectos de la cola.
    assert job.intentos_clasificacion == 1
    assert job.estado_clasificacion == "pendiente"


def test_el_limite_por_run_deja_el_resto_en_cola(sesion):
    prepara(sesion)
    provider = FakeProvider([veredicto()])

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1"), raw("2"), raw("3")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        max_clasificaciones=2,
    )

    clasificadas = sesion.scalars(select(Job).where(Job.estado_clasificacion == "clasificada")).all()
    pendientes = sesion.scalars(select(Job).where(Job.estado_clasificacion == "pendiente")).all()
    assert len(clasificadas) == 2
    assert len(pendientes) == 1


def test_un_run_posterior_recoge_las_pendientes(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1"), raw("2")])
    queries = [SearchQuery(nombre="x", texto="")]

    ejecuta_run(sesion, fuentes=[fuente], queries=queries, provider=FakeProvider([veredicto()]), max_clasificaciones=1)
    ejecuta_run(sesion, fuentes=[fuente], queries=queries, provider=FakeProvider([veredicto()]), max_clasificaciones=5)

    pendientes = sesion.scalars(select(Job).where(Job.estado_clasificacion == "pendiente")).all()
    assert pendientes == []
    assert len(sesion.scalars(select(Clasificacion)).all()) == 2


def test_sin_perfil_el_run_falla_con_un_mensaje_claro(sesion):
    import pytest

    with pytest.raises(RuntimeError, match="No hay perfil"):
        ejecuta_run(
            sesion,
            fuentes=[FakeSource([])],
            queries=[SearchQuery(nombre="x", texto="")],
            provider=FakeProvider([]),
        )


# --- B1: estado terminal al agotar los intentos --------------------------------


def agota_intentos(sesion, fuente, queries, veces=MAX_INTENTOS) -> None:
    for _ in range(veces):
        ejecuta_run(
            sesion,
            fuentes=[fuente],
            queries=queries,
            provider=FakeProvider([], error=RuntimeError("timeout")),
            dormir=Reloj(),
        )


def test_tras_agotar_los_intentos_la_oferta_queda_en_estado_terminal(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1")])
    queries = [SearchQuery(nombre="x", texto="")]

    agota_intentos(sesion, fuente, queries)

    job = sesion.scalar(select(Job))
    assert job.intentos_clasificacion == MAX_INTENTOS
    assert job.estado_clasificacion == ESTADO_AGOTADA
    assert job.estado_clasificacion != "pendiente"


def test_una_oferta_agotada_no_vuelve_a_gastar_llamadas_al_llm(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1")])
    queries = [SearchQuery(nombre="x", texto="")]
    agota_intentos(sesion, fuente, queries)

    provider = FakeProvider([veredicto()])
    ejecuta_run(sesion, fuentes=[fuente], queries=queries, provider=provider, dormir=Reloj())

    assert provider.llamadas == []
    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == ESTADO_AGOTADA
    assert job.intentos_clasificacion == MAX_INTENTOS


def test_las_ofertas_agotadas_siguen_siendo_localizables_por_estado(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1")])
    queries = [SearchQuery(nombre="x", texto="")]

    agota_intentos(sesion, fuente, queries)

    agotadas = sesion.scalars(
        select(Job).where(Job.estado_clasificacion == ESTADO_AGOTADA)
    ).all()
    assert len(agotadas) == 1
    assert agotadas[0].external_id == "1"


def test_el_run_cuenta_las_ofertas_agotadas(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1")])
    queries = [SearchQuery(nombre="x", texto="")]

    for _ in range(MAX_INTENTOS - 1):
        ejecuta_run(
            sesion,
            fuentes=[fuente],
            queries=queries,
            provider=FakeProvider([], error=RuntimeError("timeout")),
            dormir=Reloj(),
        )
    run = ejecuta_run(
        sesion,
        fuentes=[fuente],
        queries=queries,
        provider=FakeProvider([], error=RuntimeError("timeout")),
        dormir=Reloj(),
    )

    assert run.stats["_totales"]["agotadas"] == 1


def test_una_pendiente_heredada_con_los_intentos_gastados_pasa_a_terminal(sesion):
    """Filas que dejó la versión anterior: agotadas pero aún en `pendiente`."""
    prepara(sesion)
    sesion.add(
        Job(
            fuente="fake",
            external_id="viejo",
            url="https://example.com/viejo",
            titulo="Backend Developer",
            empresa="Vieja",
            descripcion="descripcion larga suficiente para pasar el prefiltro",
            hash_dedup="hash-viejo",
            estado_clasificacion="pendiente",
            intentos_clasificacion=MAX_INTENTOS,
        )
    )
    sesion.commit()
    provider = FakeProvider([veredicto()])

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        dormir=Reloj(),
    )

    job = sesion.scalar(select(Job))
    assert job.estado_clasificacion == ESTADO_AGOTADA
    assert provider.llamadas == []


# --- B3: circuit breaker por cuota agotada -------------------------------------


def test_la_cuota_agotada_corta_las_llamadas_al_proveedor(sesion):
    prepara(sesion)
    provider = FakeProvider([], error=CuotaAgotadaError("sin cuota"))

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1"), raw("2"), raw("3")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=provider,
        dormir=Reloj(),
    )

    assert len(provider.llamadas) == 1


def test_la_cuota_agotada_no_gasta_intentos_ni_reintentos(sesion):
    prepara(sesion)
    reloj = Reloj()

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1"), raw("2"), raw("3")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([], error=CuotaAgotadaError("sin cuota")),
        dormir=reloj,
    )

    jobs = sesion.scalars(select(Job)).all()
    assert len(jobs) == 3
    assert all(j.estado_clasificacion == "pendiente" for j in jobs)
    assert all(j.intentos_clasificacion == 0 for j in jobs)
    assert reloj.esperas == []


def test_el_run_se_cierra_registrando_el_motivo_del_corte(sesion):
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([], error=CuotaAgotadaError("sin cuota")),
        dormir=Reloj(),
    )

    assert run.fin is not None
    assert run.stats["_totales"]["interrumpido_por"] == "cuota_agotada"
    assert any(e["tipo"] == "cuota" and "sin cuota" in e["error"] for e in run.errores)


def test_sin_cuota_agotada_el_run_no_se_marca_interrumpido(sesion):
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([veredicto()]),
        dormir=Reloj(),
    )

    assert run.stats["_totales"]["interrumpido_por"] is None


def test_un_run_posterior_recoge_la_cola_que_dejo_la_cuota_agotada(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1"), raw("2")])
    queries = [SearchQuery(nombre="x", texto="")]

    ejecuta_run(
        sesion,
        fuentes=[fuente],
        queries=queries,
        provider=FakeProvider([], error=CuotaAgotadaError("sin cuota")),
        dormir=Reloj(),
    )
    ejecuta_run(
        sesion, fuentes=[fuente], queries=queries, provider=FakeProvider([veredicto()]), dormir=Reloj()
    )

    assert sesion.scalars(select(Job).where(Job.estado_clasificacion == "pendiente")).all() == []
    assert len(sesion.scalars(select(Clasificacion)).all()) == 2


# --- B4: forma única de run.errores --------------------------------------------

CLAVES_ERROR = {"tipo", "fuente", "job_id", "error"}


def test_los_errores_de_fuente_y_de_clasificacion_tienen_la_misma_forma(sesion):
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[
            FakeSource([], nombre="rota", error=RuntimeError("HTTP 503")),
            FakeSource([raw("1")]),
        ],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([], error=RuntimeError("timeout")),
        dormir=Reloj(),
    )

    assert len(run.errores) == 2
    # El mismo código lee ambas entradas sin adivinar qué claves existen.
    resumen = [(e["tipo"], e["fuente"], e["job_id"], e["error"]) for e in run.errores]
    assert all(set(e) == CLAVES_ERROR for e in run.errores)
    assert {t for t, _, _, _ in resumen} == {"fuente", "clasificacion"}

    fuente_error = next(e for e in run.errores if e["tipo"] == "fuente")
    assert fuente_error["fuente"] == "rota"
    assert fuente_error["job_id"] is None
    assert "503" in fuente_error["error"]

    clasif_error = next(e for e in run.errores if e["tipo"] == "clasificacion")
    assert clasif_error["job_id"] == sesion.scalar(select(Job)).id
    assert clasif_error["fuente"] == "fake"
    assert "timeout" in clasif_error["error"]


def test_el_error_de_una_oferta_agotada_se_distingue_por_su_tipo(sesion):
    prepara(sesion)
    fuente = FakeSource([raw("1")])
    queries = [SearchQuery(nombre="x", texto="")]

    for _ in range(MAX_INTENTOS - 1):
        ejecuta_run(
            sesion,
            fuentes=[fuente],
            queries=queries,
            provider=FakeProvider([], error=RuntimeError("timeout")),
            dormir=Reloj(),
        )
    run = ejecuta_run(
        sesion,
        fuentes=[fuente],
        queries=queries,
        provider=FakeProvider([], error=RuntimeError("timeout")),
        dormir=Reloj(),
    )

    assert all(set(e) == CLAVES_ERROR for e in run.errores)
    assert [e["tipo"] for e in run.errores] == ["clasificacion_agotada"]


def test_la_entrada_de_cuota_tambien_comparte_la_forma(sesion):
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([], error=CuotaAgotadaError("sin cuota")),
        dormir=Reloj(),
    )

    assert all(set(e) == CLAVES_ERROR for e in run.errores)


def test_el_run_queda_registrado_en_la_tabla(sesion):
    prepara(sesion)

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([veredicto()]),
    )

    assert len(sesion.scalars(select(Run)).all()) == 1


# --- Enriquecimiento de las descripciones truncadas de Adzuna ------------------


def test_sin_enriquecedor_el_run_funciona_igual_que_antes(sesion):
    """El parámetro es opcional a propósito: los tests y los puntos de entrada que no
    lo pasen siguen funcionando."""
    prepara(sesion)

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource([raw("1")])],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=FakeProvider([veredicto()]),
    )

    assert "_enriquecimiento" not in run.stats


def test_el_enriquecimiento_corrige_la_modalidad_antes_del_prefiltro(sesion):
    """El test que es el spec entero en una línea.

    La API de Adzuna corta a 500 caracteres, así que la modalidad se dedujo de un
    extracto que no la menciona y la oferta quedó como "desconocida". Y la modalidad
    desconocida está EXENTA de la regla de zona (app/prefilter.py:124), de modo que una
    oferta híbrida en Sevilla se cuela hasta el clasificador aunque las zonas aceptadas
    sean otras.

    Con el paso, la modalidad pasa a "hibrido", la regla de zona por fin se evalúa y la
    oferta se descarta sin gastar una llamada al LLM. El sentido de la flecha es el
    contrario del que parece: el paso no rescata ofertas del prefiltro, hace que el
    prefiltro funcione.
    """
    prepara(sesion, Preferencias(modalidades=["remoto", "hibrido"], zonas=["barcelona"]))
    provider = FakeProvider([veredicto()])
    texto_completo = (
        "Buscamos desarrollador backend para el equipo de plataforma. "
        "El puesto es para nuestra oficina de Sevilla en formato Híbrido."
    )

    run = ejecuta_run(
        sesion,
        fuentes=[
            FakeSource(
                [
                    raw(
                        "1",
                        fuente="adzuna",
                        url="https://www.adzuna.es/details/1",
                        ubicacion="Sevilla",
                        modalidad="desconocida",
                        descripcion="Buscamos desarrollador backend para el equipo…",
                        descripcion_truncada=True,
                    )
                ]
            )
        ],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=provider,
        enriquecedor=lambda url: texto_completo,
    )

    job = sesion.scalar(select(Job))
    assert job.modalidad == "hibrido"
    assert job.descripcion == texto_completo
    assert job.descripcion_truncada is False
    assert job.estado_clasificacion == "descartada_por_regla"
    assert "Sevilla" in job.motivo_regla
    assert provider.llamadas == []
    assert run.stats["_enriquecimiento"]["completadas"] == 1


def test_los_fallos_del_enriquecimiento_quedan_registrados_en_el_run(sesion):
    prepara(sesion)

    def scraper_roto(url: str) -> str:
        raise RuntimeError("timeout")

    run = ejecuta_run(
        sesion,
        fuentes=[
            FakeSource(
                [
                    raw(
                        "1",
                        fuente="adzuna",
                        url="https://www.adzuna.es/details/1",
                        descripcion_truncada=True,
                    )
                ]
            )
        ],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=FakeProvider([veredicto()]),
        enriquecedor=scraper_roto,
    )

    fallo = next(e for e in run.errores if e["tipo"] == "enriquecimiento")
    assert fallo["fuente"] == "adzuna"
    assert "timeout" in fallo["error"]
    assert run.stats["_enriquecimiento"]["fallidas"] == 1


def _oferta_del_atraso(sesion) -> Job:
    """Una oferta de un run anterior: truncada y ya juzgada con el extracto."""
    job = Job(
        fuente="adzuna",
        external_id="atraso",
        url="https://www.adzuna.es/details/atraso",
        titulo="Backend Developer",
        empresa="Empresa",
        ubicacion="Barcelona",
        modalidad="desconocida",
        descripcion="Buscamos desarrollador backend para el equipo…",
        descripcion_truncada=True,
        hash_dedup="hash-atraso",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.commit()
    sesion.add(
        Clasificacion(
            job_id=job.id,
            categoria="revisar",
            confianza="media",
            razonamiento="Juzgada con el extracto de 500 caracteres.",
            ejes={"tecnico": "ok", "seniority": "ok", "modalidad": "?", "salario": "?",
                  "sector": "ok"},
            modelo="deepseek-v4-flash",
            prompt_version=1,
        )
    )
    sesion.commit()
    return job


def test_una_oferta_del_atraso_se_reclasifica_en_el_mismo_run(sesion):
    """El enriquecimiento va ANTES de cargar `pendientes`, no sólo antes del bucle.

    La diferencia sólo se ve con una oferta que el reset devuelve a la cola. Para las
    recién ingeridas da igual dónde se ponga la llamada: el identity map de SQLAlchemy
    hace que `pendientes` y el paso compartan las mismas instancias, así que el prefiltro
    ve la modalidad corregida en cualquiera de las dos posiciones.

    Aquí no: una oferta que estaba en "clasificada" sólo entra en `pendientes` si el
    reset ocurrió antes de la consulta. El spec lo fija —"se reclasifica en el bucle de
    ese mismo run"— y sin este test mover la llamada tres líneas más abajo retrasaría un
    día cada oferta del atraso sin que nadie se enterase.
    """
    prepara(sesion, Preferencias(modalidades=["remoto", "hibrido"], zonas=["barcelona"]))
    provider = FakeProvider([veredicto("aplicar_ya")])
    texto_completo = (
        "Buscamos desarrollador backend para el equipo de plataforma. "
        "El puesto es para nuestra oficina de Barcelona en formato Híbrido."
    )
    job = _oferta_del_atraso(sesion)

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([])],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=provider,
        enriquecedor=lambda url: texto_completo,
    )

    sesion.refresh(job)
    assert job.modalidad == "hibrido"
    assert job.estado_clasificacion == "clasificada"
    assert job.clasificacion.categoria == "aplicar_ya"
    assert len(provider.llamadas) == 1


def test_el_tope_de_scrapes_llega_hasta_el_paso(sesion):
    """`max_scrapes` es un passthrough, y su valor por defecto coincide con el de
    `enriquece_descripciones`: sin pasar un valor distinto, ignorar el parámetro no lo
    detectaría nadie y el run drenaría el atraso entero de una sentada."""
    prepara(sesion)
    truncadas = [
        raw(
            str(i),
            fuente="adzuna",
            url=f"https://www.adzuna.es/details/{i}",
            descripcion_truncada=True,
        )
        for i in range(3)
    ]

    run = ejecuta_run(
        sesion,
        fuentes=[FakeSource(truncadas)],
        queries=[SearchQuery(nombre="x", texto="backend")],
        provider=FakeProvider([veredicto(), veredicto(), veredicto()]),
        enriquecedor=lambda url: "Texto completo de la oferta, con todo el detalle.",
        max_scrapes=1,
    )

    assert run.stats["_enriquecimiento"]["intentadas"] == 1
