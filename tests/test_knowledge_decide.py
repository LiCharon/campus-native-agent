from campus_desk.knowledge.decide import ClarifyDecider


def test_decide_ask_when_info_incomplete():
    class Fake:  # LLM 输出 ask
        def invoke(self, messages):
            return type("M", (), {"content":
                '{"action":"ask","questions":["您问的是哪个校区？"],"reply":"请补充校区信息。","summary":"问校区"}'})()

    d = ClarifyDecider(llm=Fake()).decide(history=[], user_text="图书馆几点开门", missed=True)
    assert d.action == "ask"
    assert d.questions == ["您问的是哪个校区？"]


def test_decide_handoff_when_question_complete():
    class Fake:  # LLM 输出 handoff
        def invoke(self, messages):
            return type("M", (), {"content":
                '{"action":"handoff","questions":[],"reply":"该问题需人工处理。","summary":"转人工"}'})()

    d = ClarifyDecider(llm=Fake()).decide(history=[], user_text="量子力学怎么学", missed=True)
    assert d.action == "handoff"


def test_decide_fallback_on_llm_error():
    class Boom:
        def invoke(self, messages):
            raise RuntimeError("down")

    d = ClarifyDecider(llm=Boom()).decide(history=[], user_text="随便", missed=True)
    assert d.action == "handoff"  # 兜底转人工，绝不裸奔
