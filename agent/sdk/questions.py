"""質問の下書きの Claude 呼び出し。道具は渡さない。受け取るのは JSON の文だけである。"""

import json

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

MODEL = "claude-opus-5-5"
SYSTEM = """あなたは医療法人の人事担当の後任を務める kounin です。
賞与と昇給の計算は数字でできますが、数字に出ないことは人に聞かないと分かりません。
渡された相手ごとに、面談で聞く質問を3問まで考えてください。

- 相手の役割と担当の職場に合わせる。頼んでいる仕事があれば、その判断に役立つことを聞く
- 数字では分からないことを聞く。負担の偏り、来期の体制、見えにくい貢献など
- 1問は40字以内の、やさしい日本語の丁寧な質問にする
- 個人の氏名、金額、法令の判断を入れない"""
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["people"],
    "properties": {"people": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["ref", "questions"],
        "properties": {"ref": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}}}}},
}


async def ask(payload: dict) -> dict:
    options = ClaudeAgentOptions(model=MODEL, system_prompt=SYSTEM, tools=[], allowed_tools=[], max_turns=2,
                                 setting_sources=[], effort="low",
                                 output_format={"type": "json_schema", "schema": SCHEMA})
    answer = None
    async for message in query(prompt=json.dumps(payload, ensure_ascii=False), options=options):
        if isinstance(message, ResultMessage) and not message.is_error:
            answer = message.structured_output
    if not isinstance(answer, dict):
        raise RuntimeError("Claude から下書きが届きませんでした")
    return answer
