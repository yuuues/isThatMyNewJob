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


def test_los_dos_modelos_de_gemini_son_independientes(monkeypatch):
    """Clasificar y extraer el CV tienen exigencias opuestas: volumen barato frente a
    una única llamada donde manda la calidad. Compartir modelo obliga a elegir mal."""
    monkeypatch.setenv("MODELO_GEMINI", "gemini-3.1-flash-lite")
    monkeypatch.setenv("MODELO_PERFIL", "gemini-3.6-flash")

    settings = Settings()

    assert settings.modelo_gemini == "gemini-3.1-flash-lite"
    assert settings.modelo_perfil == "gemini-3.6-flash"
