from app.config import Settings
from app.llm.base import LLMProvider


def crear_provider(settings: Settings) -> LLMProvider:
    if settings.proveedor_clasificacion == "deepseek":
        from app.llm.deepseek import DeepSeekProvider

        return DeepSeekProvider(
            api_key=settings.deepseek_api_key, modelo=settings.modelo_deepseek
        )

    from app.llm.gemini import GeminiProvider

    return GeminiProvider(api_key=settings.gemini_api_key, modelo=settings.modelo_gemini)
