"""M1-ZJUT 环境验证 CLI 入口（薄入口，逻辑在 campus_desk.env_check）。

用法：py scripts/verify_env.py
退出码：0 = 全部通过或跳过；1 = 存在 FAIL。
FC 探测结果单独打印（FC_SUPPORTED: True/False/未探测），供 M2 工具管道定真 FC / 伪 FC。
"""

import sys

from campus_desk.env_check import (
    check_deepseek_structured,
    check_fc_support,
    check_langgraph_quickstart,
)


def main() -> int:
    checks = [
        check_langgraph_quickstart(),
        check_deepseek_structured(),
        check_fc_support(),
    ]
    print(f"{'检查项':<26}{'结果':<6}说明")
    print("-" * 80)
    for check in checks:
        print(f"{check.name:<26}{check.status:<6}{check.detail}")
    fc = checks[2]
    fc_supported = {"PASS": "True", "FAIL": "False"}.get(fc.status, "未探测（SKIP）")
    print(f"FC_SUPPORTED: {fc_supported}")
    failed = [c for c in checks if c.status == "FAIL"]
    print("-" * 80)
    if failed:
        print(f"FAIL：{len(failed)} 项未通过，见上方说明")
        return 1
    print("全部通过（SKIP 项需外部环境，按规范不进 CI）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
