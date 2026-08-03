"""Reintentos con backoff y corte por cuota agotada.

Dos piezas complementarias:

- `con_reintentos` cubre lo que puede ser pasajero — timeout, error del proveedor,
  JSON inválido — con backoff exponencial. El spec pide 2 reintentos antes de aplazar
  la oferta al run siguiente.
- La cuota agotada es lo contrario de pasajero: reintentarla sólo gasta lo que ya no
  queda. Va en `NO_REINTENTABLES` para que salga a la primera.

El disyuntor no necesita estado propio ni un objeto aparte: la condición que corta es
la propia `CuotaAgotadaError`, que atraviesa esta función sin reintentos y llega al
pipeline, que abandona el bucle y cierra el run. Un breaker con contadores y ventana
temporal sería infraestructura sin uso: el run es un proceso corto que se ejecuta una
vez al día y no sobrevive para recordar nada.
"""

import time
from collections.abc import Callable
from typing import TypeVar

from app.llm.base import CuotaAgotadaError

T = TypeVar("T")

REINTENTOS = 2  # 2 reintentos = 3 intentos en total, como pide el spec
ESPERA_INICIAL = 1.0  # segundos
FACTOR_BACKOFF = 2.0

NO_REINTENTABLES: tuple[type[BaseException], ...] = (CuotaAgotadaError,)


def con_reintentos(
    operacion: Callable[[], T],
    *,
    reintentos: int = REINTENTOS,
    espera_inicial: float = ESPERA_INICIAL,
    factor: float = FACTOR_BACKOFF,
    dormir: Callable[[float], None] = time.sleep,
    no_reintentables: tuple[type[BaseException], ...] = NO_REINTENTABLES,
) -> T:
    """Ejecuta `operacion` hasta `1 + reintentos` veces, esperando más cada vez.

    `dormir` se inyecta para que los tests no duerman de verdad. Si se agotan los
    reintentos se propaga la última excepción tal cual: quien llama decide qué hacer.
    """
    espera = espera_inicial

    for intento in range(reintentos + 1):
        try:
            return operacion()
        except no_reintentables:
            raise
        except Exception:  # noqa: BLE001 - se reintenta cualquier fallo pasajero
            if intento == reintentos:
                raise
            dormir(espera)
            espera *= factor

    raise AssertionError("inalcanzable: el bucle siempre devuelve o propaga")
