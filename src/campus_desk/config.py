"""全局配置：从 .env 加载环境变量，pydantic-settings 提供类型安全。

变量名（惯例命名，见 .env.example）：
- DEEPSEEK_API_KEY        DeepSeek API key（OpenAI 兼容）
- LANGFUSE_PUBLIC_KEY     Langfuse 公钥（trace 埋点）
- LANGFUSE_SECRET_KEY     Langfuse 私钥
- LANGFUSE_HOST           Langfuse 服务地址（开发期 Cloud 免费额度）
- DATABASE_URL            业务数据源（mysql+pymysql://...；测试注入 SQLite）
- QDRANT_URL             向量库地址（http(s)://host:6333 或本地路径；空=不启用，
                        检索走 MySQL 稠密向量 numpy 兜底 + 关键词保底）
- BGE_LOCAL_PATH        bge 稠密模型本地目录（GCS 手动拉取的 fast-bge-small-zh-v1.5 解压目录）；
                        非空则 embeddings 用 specific_model_path 直接加载，绕开不可达的 HF 主源
- BM25_LOCAL_PATH       BM25 稀疏模型本地快照目录（HF 手动拉取的 Qdrant/bm25 解压快照）；
                        非空则 embeddings 用 specific_model_path 直接加载，绕开不可达的 HF 主源
                        （中文稀疏须配合 embeddings.py 的 jieba 分词注入，否则整段中文成单一 token）
- JWT_SECRET / JWT_EXPIRE_MINUTES  M6 登录鉴权
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
    # 业务数据源：mysql+pymysql://...（空串 = 未配置，业务运行会报错，测试注入 SQLite）
    database_url: str = ""
    # 向量库：空串=不启用（检索走 MySQL 稠密向量兜底）；本地试跑可填路径或 http 地址
    qdrant_url: str = ""
    # bge 稠密模型本地目录：GCS 手动拉取的 fast-bge-small-zh-v1.5 解压目录；
    # 非空 → embeddings 用 specific_model_path 直接加载，绕开不可达的 HF 主源（默认空=走 HF）
    bge_local_path: str = ""
    # BM25 稀疏模型本地快照目录：HF 手动拉取的 Qdrant/bm25 解压快照；
    # 非空 → embeddings 用 specific_model_path 直接加载（默认空=走 HF）。中文须配合 jieba 分词注入
    bm25_local_path: str = ""
    # 登录鉴权：JWT 密钥（演示环境默认值 ≥32 字节，生产必须改）与过期分钟数
    jwt_secret: str = "dev-secret-change-me-0123456789abcdef"
    jwt_expire_minutes: int = 1440

    @property
    def langfuse_enabled(self) -> bool:
        """埋点开关：公钥私钥都配了才算启用。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
