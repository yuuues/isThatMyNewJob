from datetime import datetime

from app.feedback import (
    MAX_CARACTERES_MOTIVO,
    PRESUPUESTO_CARACTERES,
    coste_ejemplos,
    ejemplos_few_shot,
)
from app.models import Decision, Job


def crea_job(sesion, sufijo: str, titulo: str = "Backend Developer") -> Job:
    job = Job(
        fuente="test",
        external_id=sufijo,
        url=f"https://example.com/{sufijo}",
        titulo=titulo,
        empresa=f"Empresa {sufijo}",
        descripcion="descripción de la oferta",
        hash_dedup=f"hash{sufijo}",
        estado_clasificacion="clasificada",
    )
    sesion.add(job)
    sesion.flush()
    return job


def test_solo_devuelve_decisiones_con_motivo(sesion):
    con_motivo = crea_job(sesion, "1")
    sin_motivo = crea_job(sesion, "2")
    sesion.add(Decision(job_id=con_motivo.id, estado="guardada", motivo="Stack que quiero"))
    sesion.add(Decision(job_id=sin_motivo.id, estado="descartada_por_mi", motivo=""))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    assert len(ejemplos) == 1
    assert ejemplos[0].motivo == "Stack que quiero"


def test_equilibra_ejemplos_positivos_y_negativos(sesion):
    for i in range(6):
        job = crea_job(sesion, f"p{i}")
        sesion.add(Decision(job_id=job.id, estado="guardada", motivo=f"me gusta {i}"))
    for i in range(2):
        job = crea_job(sesion, f"n{i}")
        sesion.add(Decision(job_id=job.id, estado="descartada_por_mi", motivo=f"no me gusta {i}"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=6)

    positivos = [e for e in ejemplos if e.estado == "guardada"]
    negativos = [e for e in ejemplos if e.estado == "descartada_por_mi"]
    assert len(ejemplos) == 6
    assert len(negativos) == 2
    assert len(positivos) == 4


def test_prioriza_las_decisiones_mas_recientes(sesion):
    antigua = crea_job(sesion, "vieja", titulo="Oferta antigua")
    reciente = crea_job(sesion, "nueva", titulo="Oferta reciente")
    sesion.add(
        Decision(
            job_id=antigua.id,
            estado="guardada",
            motivo="antigua",
            creada_en=datetime(2026, 1, 1),
        )
    )
    sesion.add(
        Decision(
            job_id=reciente.id,
            estado="guardada",
            motivo="reciente",
            creada_en=datetime(2026, 8, 1),
        )
    )
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=1)

    assert ejemplos[0].motivo == "reciente"


def test_aplicada_cuenta_como_ejemplo_positivo(sesion):
    job = crea_job(sesion, "a")
    sesion.add(Decision(job_id=job.id, estado="aplicada", motivo="apliqué"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    assert ejemplos[0].positivo is True


def test_guardada_y_en_proceso_cuentan_como_ejemplos_positivos(sesion):
    guardada = crea_job(sesion, "g")
    en_proceso = crea_job(sesion, "p")
    sesion.add(Decision(job_id=guardada.id, estado="guardada", motivo="me interesa"))
    sesion.add(Decision(job_id=en_proceso.id, estado="en_proceso", motivo="me llamaron"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    assert len(ejemplos) == 2
    assert all(e.positivo for e in ejemplos)


def test_descartada_por_mi_cuenta_como_ejemplo_negativo(sesion):
    job = crea_job(sesion, "d")
    sesion.add(Decision(job_id=job.id, estado="descartada_por_mi", motivo="no me interesa"))
    sesion.commit()

    ejemplo = ejemplos_few_shot(sesion)[0]

    assert ejemplo.positivo is False
    assert ejemplo.negativo is True


def test_rechazado_por_ellos_no_es_un_ejemplo_negativo(sesion):
    """El punto crítico del diseño.

    Que una empresa rechace al candidato no dice nada sobre lo que el candidato
    quiere: dice que encajaba lo bastante como para presentarse. Contarlo como
    negativo enseñaría al clasificador a esconder justo las ofertas que mejor
    encajan, y el fallo sería invisible.
    """
    rechazado = crea_job(sesion, "r", titulo="Oferta que me rechazaron")
    descartado = crea_job(sesion, "d", titulo="Oferta que descarté yo")
    sesion.add(Decision(job_id=rechazado.id, estado="rechazado_por_ellos", motivo="no siguieron"))
    sesion.add(Decision(job_id=descartado.id, estado="descartada_por_mi", motivo="no me gusta"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion)

    negativos = [e for e in ejemplos if not e.positivo]
    assert [e.titulo for e in negativos] == ["Oferta que descarté yo"]
    assert all(e.estado != "rechazado_por_ellos" for e in ejemplos)


def test_rechazado_por_ellos_tampoco_es_un_ejemplo_positivo(sesion):
    job = crea_job(sesion, "r")
    sesion.add(Decision(job_id=job.id, estado="rechazado_por_ellos", motivo="no siguieron"))
    sesion.commit()

    assert ejemplos_few_shot(sesion) == []


def test_una_racha_de_rechazos_no_desequilibra_los_ejemplos(sesion):
    """Si los rechazos se colaran como negativos, el equilibrado los dejaría
    ocupar media cuota y el prompt quedaría lleno de falsos negativos."""
    for i in range(6):
        job = crea_job(sesion, f"r{i}")
        sesion.add(Decision(job_id=job.id, estado="rechazado_por_ellos", motivo=f"fuera {i}"))
    for i in range(4):
        job = crea_job(sesion, f"p{i}")
        sesion.add(Decision(job_id=job.id, estado="aplicada", motivo=f"me presenté {i}"))
    job = crea_job(sesion, "n")
    sesion.add(Decision(job_id=job.id, estado="descartada_por_mi", motivo="no me gusta"))
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=6)

    assert len(ejemplos) == 5
    assert len([e for e in ejemplos if not e.positivo]) == 1


def test_sin_decisiones_devuelve_lista_vacia(sesion):
    assert ejemplos_few_shot(sesion) == []


def test_un_motivo_muy_largo_se_trunca(sesion):
    job = crea_job(sesion, "1")
    sesion.add(Decision(job_id=job.id, estado="guardada", motivo="x" * 5000))
    sesion.commit()

    ejemplo = ejemplos_few_shot(sesion)[0]

    assert len(ejemplo.motivo) <= MAX_CARACTERES_MOTIVO
    assert ejemplo.motivo.startswith("xxx")


def test_los_ejemplos_cortos_no_se_tocan(sesion):
    job = crea_job(sesion, "1", titulo="Backend Developer")
    sesion.add(Decision(job_id=job.id, estado="guardada", motivo="Stack que quiero"))
    sesion.commit()

    ejemplo = ejemplos_few_shot(sesion)[0]

    assert ejemplo.motivo == "Stack que quiero"
    assert ejemplo.titulo == "Backend Developer"
    assert ejemplo.empresa == "Empresa 1"


def test_el_conjunto_de_ejemplos_no_supera_el_presupuesto(sesion):
    for i in range(6):
        job = crea_job(sesion, f"p{i}")
        sesion.add(Decision(job_id=job.id, estado="guardada", motivo="me gusta " * 200))
    for i in range(6):
        job = crea_job(sesion, f"n{i}")
        sesion.add(
            Decision(job_id=job.id, estado="descartada_por_mi", motivo="no me gusta " * 200)
        )
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=8)

    assert coste_ejemplos(ejemplos) <= PRESUPUESTO_CARACTERES
    assert len(ejemplos) < 8, "el presupuesto tiene que recortar el número de ejemplos"
    assert [e for e in ejemplos if e.positivo], "el recorte no puede dejar sin positivos"
    assert [e for e in ejemplos if not e.positivo], "el recorte no puede dejar sin negativos"


def test_el_equilibrio_se_mantiene_con_motivos_largos(sesion):
    for i in range(6):
        job = crea_job(sesion, f"p{i}")
        sesion.add(Decision(job_id=job.id, estado="guardada", motivo="me gusta " * 100))
    for i in range(2):
        job = crea_job(sesion, f"n{i}")
        sesion.add(
            Decision(job_id=job.id, estado="descartada_por_mi", motivo="no me gusta " * 100)
        )
    sesion.commit()

    ejemplos = ejemplos_few_shot(sesion, maximo=6)

    positivos = [e for e in ejemplos if e.estado == "guardada"]
    negativos = [e for e in ejemplos if e.estado == "descartada_por_mi"]
    assert len(ejemplos) == 6
    assert len(negativos) == 2
    assert len(positivos) == 4
    assert all(len(e.motivo) <= MAX_CARACTERES_MOTIVO for e in ejemplos)
    assert coste_ejemplos(ejemplos) <= PRESUPUESTO_CARACTERES
