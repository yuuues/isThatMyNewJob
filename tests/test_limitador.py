from app.limitador import LimitadorPorHost, sin_espera

REMOTIVE = "https://remotive.com/api/remote-jobs"
ARBEITNOW = "https://www.arbeitnow.com/api/job-board-api"


class RelojFalso:
    """Reloj monótono controlado por el test: nadie duerme de verdad."""

    def __init__(self) -> None:
        self.ahora = 0.0

    def __call__(self) -> float:
        return self.ahora

    def avanza(self, segundos: float) -> None:
        self.ahora += segundos


def monta(intervalo: float = 2.0, **kwargs):
    """Limitador con reloj falso. `dormir` no duerme: adelanta el reloj, como haría
    una espera real, y deja constancia de cuánto se pidió esperar."""
    reloj = RelojFalso()
    dormidas: list[float] = []

    def dormir(segundos: float) -> None:
        dormidas.append(segundos)
        reloj.avanza(segundos)

    limitador = LimitadorPorHost(
        intervalo_por_defecto=intervalo, reloj=reloj, dormir=dormir, **kwargs
    )
    return limitador, reloj, dormidas


def test_la_primera_peticion_a_un_host_no_espera():
    limitador, _, dormidas = monta()

    assert limitador.espera_turno(REMOTIVE) == 0.0
    assert dormidas == []


def test_una_segunda_peticion_inmediata_espera_el_intervalo_configurado():
    limitador, _, dormidas = monta(intervalo=2.0)

    limitador.espera_turno(REMOTIVE)
    esperado = limitador.espera_turno(REMOTIVE)

    assert esperado == 2.0
    assert dormidas == [2.0]


def test_solo_espera_el_tiempo_que_falta():
    limitador, reloj, dormidas = monta(intervalo=2.0)

    limitador.espera_turno(REMOTIVE)
    reloj.avanza(1.5)
    limitador.espera_turno(REMOTIVE)

    assert dormidas == [0.5]


def test_si_ya_paso_el_intervalo_no_espera():
    limitador, reloj, dormidas = monta(intervalo=2.0)

    limitador.espera_turno(REMOTIVE)
    reloj.avanza(5)

    assert limitador.espera_turno(REMOTIVE) == 0.0
    assert dormidas == []


def test_tres_peticiones_seguidas_esperan_dos_veces():
    limitador, _, dormidas = monta(intervalo=2.0)

    for _ in range(3):
        limitador.espera_turno(REMOTIVE)

    assert dormidas == [2.0, 2.0]


def test_hosts_distintos_no_se_bloquean_entre_si():
    limitador, _, dormidas = monta(intervalo=2.0)

    limitador.espera_turno(REMOTIVE)
    limitador.espera_turno(ARBEITNOW)

    assert dormidas == []


def test_el_intervalo_se_puede_configurar_por_host():
    limitador, _, dormidas = monta(intervalo=1.0, intervalos={"remotive.com": 30.0})

    limitador.espera_turno(REMOTIVE)
    limitador.espera_turno(REMOTIVE)
    limitador.espera_turno(ARBEITNOW)
    limitador.espera_turno(ARBEITNOW)

    assert dormidas == [30.0, 1.0]


def test_un_limitador_sin_espera_nunca_duerme():
    dormidas: list[float] = []
    limitador = sin_espera()
    limitador._dormir = dormidas.append  # el intervalo es 0: no debería llamarse nunca

    for _ in range(5):
        assert limitador.espera_turno(REMOTIVE) == 0.0

    assert dormidas == []
