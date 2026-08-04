"""M1 骨架测试：包可导入 / 配置可加载 / 环境验证逻辑真实可跑。

环境验证逻辑（campus_desk.env_check）被 pytest 直接复用——脚本与测试同一实现，
脚本跑通 = 测试绿（不依赖 LLM 的项全部实跑，DeepSeek 项按规范 skip-or-pass）。
"""

import campus_desk
from campus_desk.config import settings
from campus_desk.env_check import (
    check_deepseek_call,
    check_langgraph_quickstart,
    check_sqlite_resume,
)


def test_package_version():
    assert campus_desk.__version__ == "0.1.0"


def test_settings_shape():
    # 不依赖 .env 是否已填 key：只断言结构稳定
    assert settings.langfuse_host == "https://cloud.langfuse.com"
    assert isinstance(settings.deepseek_api_key, str)


def test_langgraph_quickstart_passes():
    result = check_langgraph_quickstart()
    assert result.passed, result.detail


def test_deepseek_skip_or_pass():
    # 有 key 实跑，无 key 标 SKIP——两种情况都算通过（需外部环境项不进 CI）
    result = check_deepseek_call()
    assert result.status in ("PASS", "SKIP"), result.detail


def test_sqlite_checkpointer_resume():
    # 本地 SQLite：中断暂停 → 落库 → 新连接（模拟重启）恢复，状态完整
    result = check_sqlite_resume()
    assert result.passed, result.detail
