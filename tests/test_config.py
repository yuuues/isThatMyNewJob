from app.config import Settings


def test_settings_leen_del_entorno(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini")
    monkeypatch.setenv("PROVEEDOR_CLASIFICACION", "deepseek")

    settings = Settings()

    assert settings.gemini_api_key == "clave-gemini"
    assert settings.proveedor_clasificacion == "deepseek"


def test_settings_tienen_valores_por_defecto(monkeypatch):
    monkeypatch.delenv("PROVEEDOR_CLASIFICACION", raising=False)
    monkeypatch.delenv("MAX_CLASIFICACIONES_POR_RUN", raising=False)

    settings = Settings()

    assert settings.proveedor_clasificacion == "gemini"
    assert settings.max_clasificaciones_por_run == 200
    assert settings.ruta_bd == "data/app.db"
