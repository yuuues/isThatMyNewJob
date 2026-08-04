"""Runs que mueren sin cerrarse.

Un run que se interrumpe —matado a mano, caída del proceso, reinicio de la máquina—
deja su fila sin `fin` y el histórico lo muestra "En curso" para siempre. Pasó de
verdad: se mató un run que iba a tres horas por el modo pensamiento de DeepSeek, y su
fila se quedó viva en la vista durante el resto del día.

El arreglo se apoya en algo que sí es cierto de esta herramienta: los runs no se
solapan. El botón de "buscar ahora" tiene su propia guarda y el scheduler usa
`max_instances=1`, así que cuando arranca un run cualquier otro sin cerrar está muerto
por definición.
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Run
from app.pipeline import MOTIVO_INTERRUMPIDO, cierra_runs_colgados


def test_cierra_un_run_que_se_quedo_abierto(sesion):
    colgado = Run(inicio=datetime(2026, 8, 3, 14, 48))
    sesion.add(colgado)
    sesion.commit()

    cerrados = cierra_runs_colgados(sesion, ahora=lambda: datetime(2026, 8, 3, 17, 0))

    sesion.refresh(colgado)
    assert cerrados == 1
    assert colgado.fin == datetime(2026, 8, 3, 17, 0)


def test_deja_constancia_del_motivo(sesion):
    """Sin motivo escrito, un run cerrado a posteriori parece uno que terminó bien.

    Y usa la MISMA forma que el resto de errores del run ({tipo, fuente, job_id,
    error}): tener dos formas conviviendo fue un defecto real que hacía reventar con
    KeyError a quien leyera la clave equivocada.
    """
    colgado = Run(inicio=datetime(2026, 8, 3, 14, 48))
    sesion.add(colgado)
    sesion.commit()

    cierra_runs_colgados(sesion)

    sesion.refresh(colgado)
    interrupcion = next(e for e in colgado.errores if e.get("tipo") == MOTIVO_INTERRUMPIDO)
    assert set(interrupcion) == {"tipo", "fuente", "job_id", "error"}


def test_no_toca_los_runs_ya_cerrados(sesion):
    cerrado = Run(
        inicio=datetime(2026, 8, 3, 14, 0),
        fin=datetime(2026, 8, 3, 14, 5),
        errores=[],
    )
    sesion.add(cerrado)
    sesion.commit()

    assert cierra_runs_colgados(sesion) == 0
    sesion.refresh(cerrado)
    assert cerrado.fin == datetime(2026, 8, 3, 14, 5)
    assert cerrado.errores == []


def test_conserva_los_errores_que_ya_tuviera(sesion):
    colgado = Run(
        inicio=datetime(2026, 8, 3, 14, 48),
        errores=[{"tipo": "fuente", "fuente": "adzuna", "job_id": None, "error": "HTTP 500"}],
    )
    sesion.add(colgado)
    sesion.commit()

    cierra_runs_colgados(sesion)

    sesion.refresh(colgado)
    fuentes = [e.get("fuente") for e in colgado.errores]
    assert "adzuna" in fuentes
    assert len(colgado.errores) == 2


def test_cierra_varios_de_una_vez(sesion):
    for hora in (10, 12, 14):
        sesion.add(Run(inicio=datetime(2026, 8, 3, hora, 0)))
    sesion.commit()

    assert cierra_runs_colgados(sesion) == 3
    assert sesion.scalars(select(Run).where(Run.fin.is_(None))).all() == []


def test_sin_runs_no_hace_nada(sesion):
    assert cierra_runs_colgados(sesion) == 0


def test_un_run_nuevo_cierra_el_anterior_que_quedo_colgado(sesion, monkeypatch):
    """La integración que importa: arrancar un run limpia el cadáver del anterior."""
    from app.llm.fake import FakeProvider
    from app.models import Perfil, PreferenciasRow
    from app.pipeline import ejecuta_run
    from app.schemas import PerfilCandidato, Preferencias, SearchQuery
    from app.sources.fake import FakeSource

    sesion.add(Perfil(datos=PerfilCandidato(anios_experiencia=8).model_dump()))
    sesion.add(PreferenciasRow(datos=Preferencias().model_dump()))
    colgado = Run(inicio=datetime(2026, 8, 3, 14, 48))
    sesion.add(colgado)
    sesion.commit()

    ejecuta_run(
        sesion,
        fuentes=[FakeSource([])],
        queries=[SearchQuery(nombre="x", texto="")],
        provider=FakeProvider([]),
    )

    sesion.refresh(colgado)
    assert colgado.fin is not None, "el run muerto ya no puede figurar como en curso"


def test_un_run_reciente_sin_cerrar_sigue_contando_como_en_curso(sesion):
    """No se cierra por antigüedad, sino al arrancar otro: un run legítimo puede
    tardar horas y cerrarlo por reloj sería mentir en la otra dirección."""
    reciente = Run(inicio=datetime(2026, 8, 3, 16, 59))
    sesion.add(reciente)
    sesion.commit()

    assert reciente.fin is None
    assert reciente.inicio > datetime(2026, 8, 3, 16, 0) - timedelta(hours=1)
