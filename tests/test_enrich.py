from datetime import datetime

from sqlalchemy import select

from app.enrich import (
    MAX_FALLOS_SEGUIDOS,
    MAX_INTENTOS_SCRAPE,
    MOTIVO_RACHA,
    enriquece_descripciones,
    pendientes_de_enriquecer,
)
from app.models import Clasificacion, Decision, Job
from app.sources.adzuna_web import DescripcionNoDisponible


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
    hoy —las que se van a clasificar en este mismo run— esperasen cuatro días.

    Los `external_id` van a contrapelo del orden esperado a propósito. Con "vieja" y
    "nueva" el test pasaba también SIN `order_by`: el filtro por fuente hace que SQLite
    recorra el índice de `UniqueConstraint("fuente", "external_id")`, que devuelve
    "nueva" primero por puro orden alfabético. La aserción se apoyaba en el orden
    incidental de un índice en vez de en la cláusula que dice comprobar.
    """
    crea_job(sesion, "a-vieja", ingerida_en=datetime(2026, 8, 1, 10, 0))
    crea_job(sesion, "z-nueva", ingerida_en=datetime(2026, 8, 6, 10, 0))

    assert [j.external_id for j in pendientes_de_enriquecer(sesion, 1)] == ["z-nueva"]


TEXTO_LARGO = (
    "Buscamos desarrollador backend con experiencia en Python.\n\n"
    "El puesto es para nuestra oficina de Sevilla en formato Híbrido."
)


def scraper_que_devuelve(texto=TEXTO_LARGO):
    def scraper(url: str) -> str:
        return texto

    return scraper


def crea_clasificacion(sesion, job, categoria="revisar") -> Clasificacion:
    fila = Clasificacion(
        job_id=job.id,
        categoria=categoria,
        confianza="media",
        razonamiento="Juzgada con el extracto de 500 caracteres.",
        ejes={"tecnico": "ok", "seniority": "ok", "modalidad": "?", "salario": "?", "sector": "ok"},
        modelo="deepseek-v4-flash",
        prompt_version=1,
    )
    sesion.add(fila)
    sesion.commit()
    return fila


def test_guarda_el_texto_completo_y_apaga_la_marca(sesion):
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.descripcion == TEXTO_LARGO
    assert job.descripcion_truncada is False
    assert resumen.completadas == 1


def test_recalcula_la_modalidad_con_el_texto_completo(sesion):
    """El corazón del cambio.

    La modalidad se dedujo del extracto de 500 caracteres, donde no se menciona, así que
    la oferta quedó como "desconocida". Y la modalidad desconocida está exenta de la
    regla de zona del prefiltro (app/prefilter.py:124), así que una oferta híbrida en
    Sevilla se colaba entera hasta el clasificador.
    """
    job = crea_job(sesion, "1", modalidad="desconocida")

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.modalidad == "hibrido"


def test_devuelve_la_oferta_a_la_cola_y_borra_el_veredicto_viejo(sesion):
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.estado_clasificacion == "pendiente"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is None
    assert resumen.reevaluadas == 1


def test_una_descartada_por_regla_vuelve_a_la_cola_sin_motivo(sesion):
    """Son las que más lo necesitan: su descarte se decidió con una modalidad inventada."""
    job = crea_job(
        sesion,
        "1",
        estado_clasificacion="descartada_por_regla",
        motivo_regla="zona fuera de rango: Madrid",
    )

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.estado_clasificacion == "pendiente"
    assert job.motivo_regla is None


def test_no_resetea_una_oferta_que_el_usuario_ya_decidio(sesion):
    """Reopinar sobre algo que ya cerró a mano no aporta nada y la reabre en la lista."""
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)
    sesion.add(Decision(job_id=job.id, estado="descartada_por_mi", motivo="No me interesa"))
    sesion.commit()

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.descripcion == TEXTO_LARGO  # el texto sí se completa
    assert job.estado_clasificacion == "clasificada"  # pero el veredicto se respeta
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None
    assert resumen.reevaluadas == 0


def test_una_oferta_agotada_vuelve_a_la_cola_con_los_intentos_a_cero(sesion):
    """Devolverla a "pendiente" sin resetear los intentos no la reabre: la entierra.

    La selección no mira `estado_clasificacion`, así que una oferta que agotó los tres
    intentos de clasificación sin que su descripción llegara a completarse sí entra en el
    paso. Si vuelve a la cola con `intentos_clasificacion` en 3, el bucle del pipeline la
    manda al estado terminal nada más sacarla, sin clasificarla ni una vez con el texto
    completo: acabaría con la descripción entera traída y sin usar, atascada en "error"
    hasta que alguien pulsara "reintentar" a mano. `reintentar()` en
    app/web/routes_runs.py ya documenta esta misma trampa.
    """
    job = crea_job(
        sesion, "1", estado_clasificacion="error", intentos_clasificacion=3
    )

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    sesion.refresh(job)
    assert job.estado_clasificacion == "pendiente"
    assert job.intentos_clasificacion == 0


def test_una_oferta_ya_enriquecida_no_vuelve_a_entrar(sesion):
    """El test que cierra la duda del bucle infinito.

    El reset va atado al éxito del scrape, y lo primero que hace el éxito es apagar
    `descripcion_truncada`, que es la condición de la selección. Segunda pasada: nada.
    """
    crea_job(sesion, "1")

    primera = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)
    segunda = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=10)

    assert primera.completadas == 1
    # `primera.intentadas` no es redundante con `completadas`: sin ella, la única
    # aserción sobre el contador sería `segunda.intentadas == 0`, que vale 0 también si
    # nadie lo incrementa nunca.
    assert primera.intentadas == 1
    assert segunda.intentadas == 0


def test_la_modalidad_puede_venir_solo_en_el_titulo(sesion):
    """El título entra en el recálculo, no sólo la descripción.

    "Backend Developer Remoto" con un cuerpo que no menciona la modalidad es un caso
    corriente en Adzuna. Pasando sólo el texto al detector, esa oferta se quedaría en
    "desconocida" y volvería a saltarse las reglas de modalidad y de zona, que es
    exactamente lo que este paso viene a arreglar.
    """
    job = crea_job(sesion, "1", titulo="Backend Developer Remoto", modalidad="desconocida")
    neutro = "Buscamos a alguien para el equipo de plataforma y sus servicios internos."

    enriquece_descripciones(sesion, scraper=scraper_que_devuelve(neutro), max_por_run=10)

    sesion.refresh(job)
    assert job.modalidad == "remoto"


def test_el_tope_por_run_llega_hasta_la_consulta(sesion):
    """`test_respeta_el_tope` prueba el helper; esto prueba que el paso se lo pasa.

    Sin esta comprobación, ignorar `max_por_run` dentro del bucle no lo detecta nadie, y
    el run drenaría el atraso entero de una sentada en vez de los 40 previstos.
    """
    for i in range(3):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(sesion, scraper=scraper_que_devuelve(), max_por_run=2)

    assert resumen.intentadas == 2


def scraper_que_falla(error):
    def scraper(url: str) -> str:
        raise error

    return scraper


def test_un_fallo_suma_un_intento_y_deja_la_oferta_truncada(sesion):
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("timeout")), max_por_run=10
    )

    sesion.refresh(job)
    assert job.intentos_scrape == 1
    assert job.descripcion_truncada is True
    assert resumen.fallidas == 1
    assert resumen.fallos == [(job.id, "RuntimeError: timeout")]


def test_un_fallo_no_toca_la_clasificacion_existente(sesion):
    """Si no hemos podido mejorar el dato, no hay motivo para tirar el veredicto."""
    job = crea_job(sesion, "1", estado_clasificacion="clasificada")
    crea_clasificacion(sesion, job)

    enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("timeout")), max_por_run=10
    )

    sesion.refresh(job)
    assert job.estado_clasificacion == "clasificada"
    assert sesion.scalar(select(Clasificacion).where(Clasificacion.job_id == job.id)) is not None


def test_una_oferta_borrada_agota_los_intentos_de_una_vez(sesion):
    """Reintentar tres runs para confirmar que algo borrado sigue borrado es tirar cupo."""
    job = crea_job(sesion, "1")

    resumen = enriquece_descripciones(
        sesion,
        scraper=scraper_que_falla(DescripcionNoDisponible("ya no existe")),
        max_por_run=10,
    )

    sesion.refresh(job)
    assert job.intentos_scrape == MAX_INTENTOS_SCRAPE
    assert resumen.agotadas == 1
    assert resumen.fallos == [(job.id, "DescripcionNoDisponible: ya no existe")]
    assert pendientes_de_enriquecer(sesion, 10) == []


def test_una_fila_heredada_con_intentos_a_null_no_revienta_al_fallar(sesion):
    """El fallo que tumbaría un run entero, y que la suite no cubría.

    En la base real, `asegura_esquema()` añade `intentos_scrape` sin valor por defecto:
    las 138 filas anteriores a la columna la tienen a NULL. Un `job.intentos_scrape += 1`
    a secas es `None + 1`, o sea `TypeError`, y se lanza DENTRO del `except`, donde no lo
    captura nadie: se lleva por delante el run completo en la primera oferta del atraso
    que falle. `crea_job()` no lo destapa porque el `default=0` del ORM da 0, nunca NULL.
    """
    job = crea_job(sesion, "1")
    job.intentos_scrape = None
    sesion.commit()

    resumen = enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("timeout")), max_por_run=10
    )

    sesion.refresh(job)
    assert job.intentos_scrape == 1
    assert resumen.fallidas == 1


def test_una_racha_de_fallos_corta_el_paso(sesion):
    """Circuit breaker, en el espíritu del CuotaAgotadaError de pipeline.py.

    El día que Adzuna cambie el WAF y devuelva 403 a todo, sin este corte un run quemaría
    el cupo entero y tres runs bastarían para dar por perdido todo el atraso.
    """
    for i in range(10):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(
        sesion, scraper=scraper_que_falla(RuntimeError("403")), max_por_run=10
    )

    assert resumen.intentadas == MAX_FALLOS_SEGUIDOS
    assert resumen.cortado_por == MOTIVO_RACHA


def _scraper_por_guion(guion: list[str]):
    """Un scraper que hace lo que diga el guión, oferta por oferta.

    Las rachas hay que probarlas con secuencias concretas: un scraper que siempre falla
    o siempre acierta no distingue "el contador se reinicia" de "el contador no existe".
    """
    paso = {"n": 0}

    def scraper(url: str) -> str:
        turno = guion[paso["n"]]
        paso["n"] += 1
        if turno == "exito":
            return TEXTO_LARGO
        if turno == "retirada":
            raise DescripcionNoDisponible("ya no existe")
        raise RuntimeError("timeout")

    return scraper


def test_las_ofertas_borradas_no_alimentan_la_racha(sesion):
    """Un 404 demuestra que el servidor contesta, que es lo contrario de lo que la racha
    vigila. Drenar el atraso con ofertas retiradas seguidas no debe cortar nada.

    La retirada va DESPUÉS de cuatro fallos, no antes, y ahí está toda la gracia. Con
    seis retiradas seguidas el test pasaba por accidente y con las retiradas por delante
    tampoco medía nada: la rama del 404 no incrementa el contador, así que quitarle el
    reinicio da igual si el contador ya estaba a cero. Poniéndola en el quinto turno, con
    la racha a cuatro, el reinicio decide: sin él la sexta oferta corta el paso.
    """
    for i in range(8):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(
        sesion,
        scraper=_scraper_por_guion(["fallo"] * 4 + ["retirada"] + ["fallo"] * 3),
        max_por_run=8,
    )

    assert resumen.intentadas == 8
    assert resumen.agotadas == 1
    assert resumen.fallidas == 7
    assert resumen.cortado_por is None


def test_un_exito_reinicia_la_racha(sesion):
    """Cuatro fallos, un éxito y tres fallos: ocho ofertas y ningún corte.

    El guión es asimétrico a propósito. Alternando fallo y éxito nunca se juntan más de
    dos fallos, así que el reinicio no llega a ser determinante y el test pasa igual sin
    él. Aquí, sin el reinicio, el contador seguiría desde cuatro y cortaría en la sexta.
    """
    for i in range(8):
        crea_job(sesion, str(i))

    resumen = enriquece_descripciones(
        sesion,
        scraper=_scraper_por_guion(["fallo"] * 4 + ["exito"] + ["fallo"] * 3),
        max_por_run=8,
    )

    assert resumen.intentadas == 8
    assert resumen.completadas == 1
    assert resumen.cortado_por is None
