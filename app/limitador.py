"""Limitador de peticiones por host.

El spec pide rate limiting propio hacia las APIs externas "para no agotar cuotas por
ráfagas". Aquí se impone un intervalo mínimo entre dos peticiones consecutivas al
mismo host; el reloj y la espera se inyectan para poder probarlo sin dormir de verdad.

Decisión: el intervalo por defecto (1 s) protege de la ráfaga, no del volumen diario.
El volumen lo controla la ingesta descargando cada feed una sola vez por run; un
intervalo de horas aquí bloquearía el proceso en lugar de protegerlo.
"""

import time
from collections.abc import Callable
from urllib.parse import urlparse

INTERVALO_POR_DEFECTO = 1.0


class LimitadorPorHost:
    """Garantiza un intervalo mínimo entre peticiones al mismo host."""

    def __init__(
        self,
        intervalo_por_defecto: float = INTERVALO_POR_DEFECTO,
        intervalos: dict[str, float] | None = None,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self.intervalo_por_defecto = intervalo_por_defecto
        self.intervalos = dict(intervalos or {})
        self._reloj = reloj
        self._dormir = dormir
        self._ultima: dict[str, float] = {}

    def intervalo(self, host: str) -> float:
        return self.intervalos.get(host, self.intervalo_por_defecto)

    def espera_turno(self, url: str) -> float:
        """Bloquea lo justo para respetar el intervalo del host. Devuelve lo esperado.

        La marca de la última petición se calcula sumando la espera en vez de releer
        el reloj: así el resultado no depende de la resolución del reloj ni de que
        `dormir` sea real.
        """
        host = urlparse(url).hostname or url
        ahora = self._reloj()
        ultima = self._ultima.get(host)

        espera = 0.0
        if ultima is not None:
            restante = self.intervalo(host) - (ahora - ultima)
            if restante > 0:
                self._dormir(restante)
                espera = restante

        self._ultima[host] = ahora + espera
        return espera


def sin_espera() -> LimitadorPorHost:
    """Limitador inerte, para tests y para quien quiera desactivarlo explícitamente."""
    return LimitadorPorHost(intervalo_por_defecto=0.0, intervalos={})
