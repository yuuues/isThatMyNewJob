import pytest

from app.llm.base import CuotaAgotadaError
from app.resiliencia import ESPERA_INICIAL, FACTOR_BACKOFF, REINTENTOS, con_reintentos


class Reloj:
    """Sustituye a time.sleep: anota las esperas en lugar de dormirlas."""

    def __init__(self) -> None:
        self.esperas: list[float] = []

    def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)


class Operacion:
    """Falla las `fallos` primeras veces y luego devuelve `valor`."""

    def __init__(self, fallos: int, valor: str = "ok", error: Exception | None = None) -> None:
        self.fallos = fallos
        self.valor = valor
        self.error = error or RuntimeError("timeout")
        self.llamadas = 0

    def __call__(self) -> str:
        self.llamadas += 1
        if self.llamadas <= self.fallos:
            raise self.error
        return self.valor


def test_si_la_operacion_va_bien_a_la_primera_no_hay_espera():
    reloj = Reloj()
    operacion = Operacion(fallos=0)

    assert con_reintentos(operacion, dormir=reloj) == "ok"
    assert operacion.llamadas == 1
    assert reloj.esperas == []


def test_un_fallo_transitorio_se_reintenta_y_devuelve_el_resultado():
    reloj = Reloj()
    operacion = Operacion(fallos=1)

    assert con_reintentos(operacion, dormir=reloj) == "ok"
    assert operacion.llamadas == 2
    assert reloj.esperas == [ESPERA_INICIAL]


def test_el_backoff_es_exponencial():
    reloj = Reloj()
    operacion = Operacion(fallos=2)

    con_reintentos(operacion, dormir=reloj)

    assert reloj.esperas == [ESPERA_INICIAL, ESPERA_INICIAL * FACTOR_BACKOFF]


def test_agotados_los_reintentos_se_propaga_el_ultimo_error():
    reloj = Reloj()
    operacion = Operacion(fallos=99)

    with pytest.raises(RuntimeError, match="timeout"):
        con_reintentos(operacion, dormir=reloj)

    # 1 intento + REINTENTOS reintentos, ni uno más.
    assert operacion.llamadas == REINTENTOS + 1
    assert len(reloj.esperas) == REINTENTOS


def test_con_cero_reintentos_solo_hay_un_intento():
    reloj = Reloj()
    operacion = Operacion(fallos=99)

    with pytest.raises(RuntimeError):
        con_reintentos(operacion, reintentos=0, dormir=reloj)

    assert operacion.llamadas == 1
    assert reloj.esperas == []


def test_la_cuota_agotada_no_gasta_reintentos():
    reloj = Reloj()
    operacion = Operacion(fallos=99, error=CuotaAgotadaError("sin cuota"))

    with pytest.raises(CuotaAgotadaError):
        con_reintentos(operacion, dormir=reloj)

    assert operacion.llamadas == 1
    assert reloj.esperas == []


def test_el_spec_pide_dos_reintentos():
    assert REINTENTOS == 2
