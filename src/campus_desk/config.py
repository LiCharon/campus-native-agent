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
    # M3 起业务数据源：mysql+pymysql://...（空串 = 未配置，业务运行会报错，测试注入 SQLite）
    database_url: str = ""
    # M5 超时升级扫描（scheduler/escalation.py，需求 §3）：P1 超 4h / P2 超 48h
    # 升级一次（escalation 字段），PENDING_VERIFY 挂起超 72h 自动关闭；周期 60s
    escalation_p1_hours: int = 4
    escalation_p2_hours: int = 48
    auto_close_hours: int = 72
    scan_interval_seconds: int = 60
    # M6 登录鉴权：JWT 密钥（演示环境默认值 ≥32 字节，生产必须改）与过期分钟数
    jwt_secret: str = "dev-secret-change-me-0123456789abcdef"
    jwt_expire_minutes: int = 1440
    # M6 业务参数可配化：关键词表/派单映射 JSON 路径（相对仓库根）
    business_config_path: str = "config/business_rules.json"
    # M7 FAQ 热点缓存：redis://localhost:6379/0；空串 = 不启用（直查 DB，
    # 连不上自动降级不崩，见 faq_cache.py）
    redis_url: str = ""

    @property
    def langfuse_enabled(self) -> bool:
        """埋点开关：公钥私钥都配了才算启用。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
