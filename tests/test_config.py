from app.config import Settings


def test_settings_leen_del_entorno(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-gemini")
    monkeypatch.setenv("PROVEEDOR_CLASIFICACION", "deepseek")

    settings = Settings()

    assert settings.gemini_api_key == "clave-gemini"
    assert settings.proveedor_clasificacion == "deepseek"


def test_settings_tienen_valores_por_defecto(monkeypatch):
    """Se ignora el .env a propósito.

    Con `Settings()` a secas, pydantic-settings lee el fichero .env del proyecto, así
    que este test no comprobaba los valores por defecto de la clase sino el contenido
    del .env local, y se ponía rojo en cuanto alguien cambiaba una línea ahí sin tocar
    app/config.py. Pasó al cambiar el proveedor a deepseek.
    """
    monkeypatch.delenv("PROVEEDOR_CLASIFICACION", raising=False)
    monkeypatch.delenv("MAX_CLASIFICACIONES_POR_RUN", raising=False)

    settings = Settings(_env_file=None)

    assert settings.proveedor_clasificacion == "gemini"
    assert settings.max_clasificaciones_por_run == 200
    assert settings.ruta_bd == "data/app.db"


def test_el_env_local_no_influye_en_los_defaults_de_la_clase(monkeypatch):
    """Guardia del test de arriba: si vuelve a leerse el .env, esto lo detecta."""
    monkeypatch.setenv("PROVEEDOR_CLASIFICACION", "deepseek")

    assert Settings(_env_file=None).proveedor_clasificacion == "deepseek"
    monkeypatch.delenv("PROVEEDOR_CLASIFICACION")
    assert Settings(_env_file=None).proveedor_clasificacion == "gemini"


def test_los_dos_modelos_de_gemini_son_independientes(monkeypatch):
    """Clasificar y extraer el CV tienen exigencias opuestas: volumen barato frente a
    una única llamada donde manda la calidad. Compartir modelo obliga a elegir mal."""
    monkeypatch.setenv("MODELO_GEMINI", "gemini-3.1-flash-lite")
    monkeypatch.setenv("MODELO_PERFIL", "gemini-3.6-flash")

    settings = Settings()

    assert settings.modelo_gemini == "gemini-3.1-flash-lite"
    assert settings.modelo_perfil == "gemini-3.6-flash"
