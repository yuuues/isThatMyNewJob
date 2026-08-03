from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConsumoFuente


class PresupuestoAgotadoError(RuntimeError):
    """No queda cupo de peticiones para esta fuente en el periodo actual."""


class Presupuesto(Protocol):
    """Cupo de peticiones de una fuente.

    Distinto del limitador por host: aquél protege de ráfagas dentro de un run,
    éste reparte un cupo que se agota y no vuelve hasta el periodo siguiente.
    """

    def intenta_consumir(self, n: int = 1) -> bool: ...

    def restante(self) -> float: ...

    def exige(self, n: int = 1) -> None: ...


class SinLimite:
    """Para fuentes sin cupo: Adzuna, Remotive, Arbeitnow."""

    def intenta_consumir(self, n: int = 1) -> bool:
        return True

    def restante(self) -> float:
        return float("inf")

    def exige(self, n: int = 1) -> None:
        return None


class PresupuestoMensual:
    """Cupo mensual persistido, por fuente y mes natural.

    El consumo se confirma en cuanto se concede, aunque después falle el procesado
    de lo recibido: la petición ya se hizo y el crédito ya está gastado. Contarlo
    de otro modo haría que un fallo de persistencia regalase créditos que el
    proveedor sí ha descontado, y el cupo real se agotaría antes que el nuestro.
    """

    def __init__(
        self,
        sesion: Session,
        fuente: str,
        limite: int,
        reloj: Callable[[], datetime] | None = None,
    ) -> None:
        self.sesion = sesion
        self.fuente = fuente
        self.limite = limite
        self._reloj = reloj or (lambda: datetime.now(UTC))

    def _periodo(self) -> str:
        return self._reloj().strftime("%Y-%m")

    def _fila(self) -> ConsumoFuente:
        periodo = self._periodo()
        fila = self.sesion.scalar(
            select(ConsumoFuente).where(
                ConsumoFuente.fuente == self.fuente, ConsumoFuente.periodo == periodo
            )
        )
        if fila is None:
            fila = ConsumoFuente(fuente=self.fuente, periodo=periodo, peticiones=0)
            self.sesion.add(fila)
            self.sesion.flush()
        return fila

    def restante(self) -> float:
        return max(0, self.limite - self._fila().peticiones)

    def intenta_consumir(self, n: int = 1) -> bool:
        """Concede `n` peticiones o ninguna. Nunca concede de forma parcial."""
        fila = self._fila()
        if fila.peticiones + n > self.limite:
            return False
        fila.peticiones += n
        self.sesion.commit()
        return True

    def exige(self, n: int = 1) -> None:
        if not self.intenta_consumir(n):
            raise PresupuestoAgotadoError(
                f"Cupo de {self.fuente} agotado para {self._periodo()}: "
                f"{self.limite} peticiones consumidas. Se reanuda el mes que viene."
            )
