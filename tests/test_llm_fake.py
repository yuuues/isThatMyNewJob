import pytest
from pydantic import BaseModel

from app.llm.fake import FakeProvider


class Salida(BaseModel):
    valor: str


def test_el_provider_fake_devuelve_la_respuesta_programada():
    provider = FakeProvider([Salida(valor="hola")])

    resultado = provider.complete_json(system="s", user="u", modelo_salida=Salida)

    assert resultado.valor == "hola"


def test_el_provider_fake_registra_los_prompts():
    provider = FakeProvider([Salida(valor="x")])

    provider.complete_json(system="eres un evaluador", user="la oferta", modelo_salida=Salida)

    assert provider.llamadas[0]["system"] == "eres un evaluador"
    assert provider.llamadas[0]["user"] == "la oferta"


def test_el_provider_fake_puede_simular_un_fallo():
    provider = FakeProvider([], error=RuntimeError("cuota agotada"))

    with pytest.raises(RuntimeError, match="cuota agotada"):
        provider.complete_json(system="s", user="u", modelo_salida=Salida)


def test_el_provider_fake_puede_fallar_n_veces_y_luego_tener_exito():
    provider = FakeProvider([Salida(valor="ok")], error=RuntimeError("timeout"), fallos=2)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="timeout"):
            provider.complete_json(system="s", user="u", modelo_salida=Salida)

    resultado = provider.complete_json(system="s", user="u", modelo_salida=Salida)

    assert resultado.valor == "ok"
    assert len(provider.llamadas) == 3


def test_sin_fallos_declarados_el_error_se_lanza_en_todas_las_llamadas():
    provider = FakeProvider([Salida(valor="ok")], error=RuntimeError("timeout"))

    for _ in range(3):
        with pytest.raises(RuntimeError, match="timeout"):
            provider.complete_json(system="s", user="u", modelo_salida=Salida)


def test_el_provider_fake_cicla_las_respuestas_si_se_agotan():
    provider = FakeProvider([Salida(valor="a"), Salida(valor="b")])

    valores = [
        provider.complete_json(system="s", user="u", modelo_salida=Salida).valor
        for _ in range(3)
    ]

    assert valores == ["a", "b", "a"]
