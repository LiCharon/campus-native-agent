"""LLM 构造单测（M2-T4）：build_llm 保持 json_object 铁律；build_tool_llm 不带 response_format。"""

from campus_desk.llm import build_llm, build_tool_llm


def test_build_llm_keeps_json_object():
    llm = build_llm()
    assert llm.model_kwargs.get("response_format") == {"type": "json_object"}


def test_build_tool_llm_has_no_response_format():
    llm = build_tool_llm()
    assert "response_format" not in (llm.model_kwargs or {})


def test_build_tool_llm_same_model_and_base_url():
    llm = build_tool_llm()
    assert llm.model_name
    assert "deepseek" in llm.openai_api_base
