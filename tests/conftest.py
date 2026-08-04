"""测试共享设施：可控 fake LLM stub。

IntentClassifier 对 LLM 的依赖面只有一处：`llm.invoke(messages)` 返回带
`.content` 的对象（真 LLM 为 AIMessage）。fake 只对齐这个面：
- 序列元素为 str：作为 invoke 返回的 content（模拟模型输出）
- 序列元素为 Exception：invoke 时抛出（模拟 LLM 网络/服务异常）
- 序列用尽：返回"永远解析失败"的内容
"""


class FakeStructuredLLM:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.sequence:
            return type("FakeAIMessage", (), {"content": "这不是JSON"})()
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return type("FakeAIMessage", (), {"content": item})()


class FakeIntentClassifier:
    """固定返回预设 IntentResult 的 stub，供图测试注入（不依赖 LLM）。"""

    def __init__(self, result):
        self.result = result

    def classify(self, user_input):
        return self.result
