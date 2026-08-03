from app.config import Settings
from app.llm.factory import crear_provider


def test_por_defecto_crea_gemini():
    provider = crear_provider(
        Settings(proveedor_clasificacion="gemini", gemini_api_key="k")
    )

    assert provider.nombre.startswith("gemini")


def test_la_config_selecciona_deepseek():
    provider = crear_provider(
        Settings(proveedor_clasificacion="deepseek", deepseek_api_key="k")
    )

    assert provider.nombre.startswith("deepseek")
