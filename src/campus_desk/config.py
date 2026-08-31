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

# M15A-① JWT 密钥安全基线（单源：默认值常量即校验基准）
# 该默认值随仓库公开在 GitHub，任何人都能用它伪造管理员令牌。
DEFAULT_JWT_SECRET = "dev-secret-change-me-0123456789abcdef"
JWT_SECRET_MIN_LENGTH = 32  # HS256 建议 ≥32 字节，短于此 PyJWT 会告警


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash-vision-exp"
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
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expire_minutes: int = 1440
    # M15A-① 逃生阀：本地开发懒得换密钥时显式置 1 放行默认密钥。
    # ⚠️ 只放行"默认值"这一个已知弱串，自己设的短密钥照样拒。
    allow_insecure_dev: bool = False
    # M12-ZJUT 上下文窗口：注入 LLM 的"近期对话"轮数（按 user 消息计，默认最近 8 轮）。
    # 仅约束 LLM prompt（意图/追问决策/工具选择），不约束检索拼接（embedding/关键词计分）。
    context_window_rounds: int = 8
    # M13-ZJUT 成本单价（元 / 百万 token）：仅报表层按当前值派生费用（llm_usage 不存钱，
    # 改价不重算历史）。默认按 DeepSeek 公开价（输入 2 / 输出 8），换模型/调价改 .env 即可
    deepseek_input_price: float = 2.0
    deepseek_output_price: float = 8.0

    @property
    def langfuse_enabled(self) -> bool:
        """埋点开关：公钥私钥都配了才算启用。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def validate_jwt_secret(self) -> None:
        """M15A-① 启动期密钥自检：不合法直接拒绝启动（fail-fast）。

        判定只看密钥值本身，不看 APP_ENV——真实部署最典型的翻车是忘了设
        APP_ENV，那样"仅 production 才拦"的保护永远不会触发。

        Raises:
            RuntimeError: 密钥仍是公开默认值 / 与默认值高度相似 / 长度不足。
        """
        secret = (self.jwt_secret or "").strip()
        default = DEFAULT_JWT_SECRET.lower()

        is_default_like = secret.lower() == default or (
            len(secret) >= 8 and (secret.lower() in default or default in secret.lower())
        )
        if is_default_like and not self.allow_insecure_dev:
            raise RuntimeError(
                "JWT_SECRET 仍为仓库公开的默认密钥，拒绝启动。"
                "请生成新密钥：python -c \"import secrets;print(secrets.token_urlsafe(48))\"，"
                "写入 .env 的 JWT_SECRET；"
                "仅本地开发可在 .env 设 ALLOW_INSECURE_DEV=1 放行（切勿用于部署）。"
            )
        if len(secret) < JWT_SECRET_MIN_LENGTH:
            raise RuntimeError(
                f"JWT_SECRET 长度不足（当前 {len(secret)}，要求 ≥{JWT_SECRET_MIN_LENGTH} 字符），"
                "拒绝启动。请改用足够长的随机串，ALLOW_INSECURE_DEV 对此项无效。"
            )


settings = Settings()
