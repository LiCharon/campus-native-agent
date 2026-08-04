"""全局配置：从 .env 加载环境变量，pydantic-settings 提供类型安全。

变量名（惯例命名，见 .env.example）：
- DEEPSEEK_API_KEY        DeepSeek API key（OpenAI 兼容）
- LANGFUSE_PUBLIC_KEY     Langfuse 公钥（trace 埋点）
- LANGFUSE_SECRET_KEY     Langfuse 私钥
- LANGFUSE_HOST           Langfuse 服务地址（开发期 Cloud 免费额度）
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        """埋点开关：公钥私钥都配了才算启用。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
