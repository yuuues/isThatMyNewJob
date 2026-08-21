from datetime import datetime

from app.dedup import hash_dedup
from app.fusion import fusiona_duplicados
from app.models import Clasificacion, Decision, Job


def job(sufijo: str, **kwargs) -> Job:
    base = dict(
        fuente="adzuna",
        external_id=f"ext-{sufijo}",
        url=f"https://example.com/{sufijo}",
        titulo="Senior Backend Developer - Php",
        empresa="PayXpert",
        ubicacion="Madrid",
        descripcion="descripción",
        # La clave vieja, la que incluía la ubicación: es lo que hay en la base real.
        hash_dedup=f"hash-viejo-{sufijo}",
        estado_clasificacion="clasificada",
    )
    base.update(kwargs)
    return Job(**base)


def clasifica(sesion, job_id: int) -> None:
    sesion.add(
        Clasificacion(
            job_id=job_id,
            categoria="revisar",
            confianza="alta",
            razonamiento="encaja",
            modelo="fake",
            prompt_version=1,
        )
    )


def test_deja_una_sola_fila_por_oferta(sesion):
    sesion.add_all(
        [
            job("a", ubicacion="Madrid"),
            job("b", ubicacion="Málaga"),
            job("c", ubicacion="Guntín, Lugo"),
        ]
    )
    sesion.commit()

    resumen = fusiona_duplicados(sesion)

    assert sesion.query(Job).count() == 1
    assert resumen.grupos == 1
    assert resumen.borradas == 2


def test_la_fila_superviviente_acumula_todas_las_ubicaciones(sesion):
    sesion.add_all([job("a", ubicacion="Madrid"), job("b", ubicacion="Málaga")])
    sesion.commit()

    fusiona_duplicados(sesion)

    superviviente = sesion.query(Job).one()
    assert superviviente.ubicaciones == ["Madrid", "Málaga"]


def test_conserva_la_fila_sobre_la_que_ya_habias_decidido(sesion):
    sesion.add_all([job("a"), job("b", ubicacion="Málaga")])
    sesion.commit()
    decidida = sesion.query(Job).filter_by(external_id="ext-b").one()
    sesion.add(Decision(job_id=decidida.id, estado="aplicada"))
    sesion.commit()

    fusiona_duplicados(sesion)

    superviviente = sesion.query(Job).one()
    assert superviviente.external_id == "ext-b"
    assert sesion.query(Decision).count() == 1


def test_entre_dos_decisiones_gana_la_mas_reciente_y_se_avisa(sesion):
    """Ocurre de verdad: dos copias de la misma oferta, decidida cada una por su lado.
    Una de las dos decisiones se pierde, así que el comando tiene que decirlo."""
    sesion.add_all([job("a"), job("b", ubicacion="Málaga")])
    sesion.commit()
    vieja, nueva = sesion.query(Job).order_by(Job.external_id).all()
    sesion.add_all(
        [
            Decision(
                job_id=vieja.id,
                estado="descartada_por_mi",
                actualizada_en=datetime(2026, 8, 1),
            ),
            Decision(
                job_id=nueva.id, estado="aplicada", actualizada_en=datetime(2026, 8, 12)
            ),
        ]
    )
    sesion.commit()

    resumen = fusiona_duplicados(sesion)

    superviviente = sesion.query(Job).one()
    assert superviviente.id == nueva.id
    assert sesion.query(Decision).one().estado == "aplicada"
    assert len(resumen.conflictos) == 1
    assert "descartada_por_mi" in resumen.conflictos[0]


def test_dos_decisiones_iguales_no_son_un_conflicto(sesion):
    sesion.add_all([job("a"), job("b", ubicacion="Málaga")])
    sesion.commit()
    for fila in sesion.query(Job).all():
        sesion.add(Decision(job_id=fila.id, estado="descartada_por_mi"))
    sesion.commit()

    assert fusiona_duplicados(sesion).conflictos == ()


def test_prefiere_la_clasificada_a_la_descartada_por_regla(sesion):
    sesion.add_all(
        [
            job("a", estado_clasificacion="descartada_por_regla", motivo_regla="zona"),
            job("b", estado_clasificacion="clasificada", ubicacion="Málaga"),
        ]
    )
    sesion.commit()
    clasifica(sesion, sesion.query(Job).filter_by(external_id="ext-b").one().id)
    sesion.commit()

    fusiona_duplicados(sesion)

    assert sesion.query(Job).one().external_id == "ext-b"


def test_borra_las_clasificaciones_de_las_filas_que_se_van(sesion):
    """Sin esto quedan clasificaciones huérfanas apuntando a un `job_id` que ya no existe,
    y el listado revienta al recorrerlas."""
    sesion.add_all([job("a"), job("b", ubicacion="Málaga")])
    sesion.commit()
    for fila in sesion.query(Job).all():
        clasifica(sesion, fila.id)
    sesion.commit()

    fusiona_duplicados(sesion)

    assert sesion.query(Clasificacion).count() == 1
    assert sesion.query(Clasificacion).one().job_id == sesion.query(Job).one().id


def test_recalcula_la_clave_tambien_de_las_ofertas_sin_duplicado(sesion):
    """Si no, la oferta se volvería a ingerir como nueva en el run siguiente: su hash
    guardado es el viejo y ya no coincide con el que calcula la ingesta."""
    sesion.add(job("solo", titulo="Data Engineer"))
    sesion.commit()

    resumen = fusiona_duplicados(sesion)

    fila = sesion.query(Job).one()
    assert fila.hash_dedup == hash_dedup("PayXpert", "Data Engineer")
    assert resumen.grupos == 0
    assert resumen.borradas == 0


def test_es_idempotente(sesion):
    sesion.add_all([job("a"), job("b", ubicacion="Málaga")])
    sesion.commit()

    fusiona_duplicados(sesion)
    resumen = fusiona_duplicados(sesion)

    assert sesion.query(Job).count() == 1
    assert resumen.borradas == 0
    assert sesion.query(Job).one().ubicaciones == ["Madrid", "Málaga"]
