"""M13 验证脚本：真 DeepSeek 调用是否落 llm_usage（一次性端到端冒烟）。

用法（需 .env 配 DEEPSEEK_API_KEY + DATABASE_URL）：
    PYTHONPATH=src python scripts/_verify_m13_usage.py

跑一次真实意图分类（1 次 LLM 调用），查回刚写的计量行打印，然后删除该验证行
（保持业务库干净）。与 _verify_kb.py 同风格：下划线前缀 = 开发期验证脚本，非交付物。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from campus_desk import usage
from campus_desk.db.models import LLMUsage
from campus_desk.db.session import default_session_factory
from campus_desk.entry.intent import IntentClassifier

_MARK = "m13-verify"


def main() -> int:
    factory = default_session_factory()
    with usage.usage_ctx(user_id="verify-m13", thread_id=_MARK):
        result = IntentClassifier().classify("图书馆借的书逾期了怎么办？")
    print(f"意图分类结果: intent={result.intent} confidence={result.confidence}")

    with factory() as session, session.begin():
        rows = session.query(LLMUsage).filter(LLMUsage.thread_id == _MARK).all()
        if not rows:
            print("[FAIL] llm_usage 未落库——检查 handler 是否挂载 / 落库是否被吞")
            return 1
        for r in rows:
            print(
                f"[OK] call_point={r.call_point} model={r.model} "
                f"prompt={r.prompt_tokens} completion={r.completion_tokens} "
                f"total={r.total_tokens} status={r.status} user={r.user_id}"
            )
        session.query(LLMUsage).filter(LLMUsage.thread_id == _MARK).delete()
    print(f"（已清理 {len(rows)} 行验证数据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
