"""
A1 问诊交互模块。
负责与用户进行多轮对话，收集结构化症状数据包。

输出协议（需求1：选项卡）：每一轮 LLM 仅输出一个 JSON 对象——
- 提问轮：{"type":"question","question":"...","options":["...",...],"allow_free_text":true}
- 收尾轮：{"type":"summary","chief_complaint":...,"symptoms":...,"history":...,
          "medications":...,"family_history":...}
前端把 options 渲染成可点选项卡；allow_free_text 为真时同时保留自由输入。
"""

import json
from datetime import datetime
from . import ollama_client as oc

MODEL = "llama3.3:70b"


def build_system_prompt(department: str) -> str:
    return (
        f"You are a professional AI Medical Interviewer in the {department} department. "
        "Your sole job is to collect a structured health profile by asking the patient questions. "
        "You collect these fields: chief_complaint(主诉), symptoms(详细症状,起病/诱因/程度), "
        "history(既往病史/手术), medications(当前用药/补剂), family_history(家族史).\n\n"
        "STRICT OUTPUT PROTOCOL — every reply MUST be exactly ONE JSON object and NOTHING else "
        "(no markdown, no prose, no code fences):\n"
        '1. To ask a question: {"type":"question","question":"<中文问题>",'
        '"options":["<选项1>","<选项2>",...],"multi":false,"allow_free_text":true}\n'
        '2. When all fields are gathered OR the patient says they are done: '
        '{"type":"summary","chief_complaint":"...","symptoms":"...","history":"...",'
        '"medications":"...","family_history":"..."}\n\n'
        "RULES:\n"
        "- Ask ONE question at a time. Questions and options MUST be in Simplified Chinese.\n"
        "- options are concrete likely answers (病人可直接点选). Provide AS MANY options as are "
        "clinically relevant — do NOT cap at a fixed number. For symptom/detail questions "
        "(描述病情、症状清单、部位、性质等) give a COMPREHENSIVE list with NO upper limit (不设上限，宁多勿少); "
        "for simple single-choice questions a few options suffice. Minimum 2. "
        "Always keep allow_free_text=true so the patient can type something not listed.\n"
        '- Set "multi":true when MULTIPLE answers can reasonably apply at once '
        "(e.g. 同时存在的多个症状、正在服用的多种药物、多项既往史); set \"multi\":false for "
        "single-choice questions (e.g. 性别、是/否、单选程度). Default false when unsure.\n"
        f'- Your VERY FIRST question MUST ask what symptoms/discomfort the patient currently has, '
        f'offering common {department} 症状 as options, and MUST set "multi":true '
        "(患者常同时有多个症状，第一问必须可多选).\n"
        '- Do NOT include catch-all options like "其他"/"其他症状"/"以上都不是"/"更多"; '
        'the interface itself provides a "更多" button (to fetch more options) and a free-text box. '
        "Just list real, specific choices.\n"
        "- At a natural point you MUST ask whether the patient has recent lab / exam / imaging reports "
        'to provide, e.g. {"type":"question","question":"您是否有近期的化验、检查或影像报告可以提供？",'
        '"options":["有，我现在上传","暂时没有","稍后补充"],"allow_free_text":false}. '
        "The interface has an upload control for this.\n"
        "- Be professional, empathetic, concise. Do NOT give diagnosis or medical advice; only collect data.\n"
        "- Output the summary JSON only after you have enough across the fields; do not end prematurely."
    )


def _extract_json(text: str) -> dict | None:
    """从模型输出里抽取第一个平衡的 JSON 对象（容忍前后多余文字/代码围栏）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class MedicalInterviewer:
    """问诊状态机，管理多轮对话并产出结构化数据。"""

    def __init__(self, user_id: str, department: str, member_id: str | None = None):
        self.user_id = user_id
        self.department = department
        self.member_id = member_id           # 需求4：症状包归档到该成员
        self.history = []                    # [{"role": "user"/"assistant", "content": ...}]
        self.current_question = ""           # 当前待答问题（供"更多"追加选项用）
        self.current_options = []            # 当前已展示选项

    async def chat(self, message: str) -> tuple[dict, bool]:
        """进行一轮对话。

        返回 (payload, done)：
        - done=False → payload={"question","options","allow_free_text"}
        - done=True  → payload=症状数据包（summary + department + collected_at）
        """
        self.history.append({"role": "user", "content": message})

        full_prompt = ""
        for turn in self.history:
            role = "Patient" if turn["role"] == "user" else "Interviewer"
            full_prompt += f"{role}: {turn['content']}\n"
        full_prompt += "Interviewer: "

        response = await oc.generate(
            model=MODEL,
            prompt=full_prompt,
            system=build_system_prompt(self.department),
            options={"temperature": 0.3},
        )
        self.history.append({"role": "assistant", "content": response})

        parsed = _extract_json(response)

        if parsed and parsed.get("type") == "summary":
            parsed.pop("type", None)
            parsed["department"] = self.department
            parsed["collected_at"] = datetime.utcnow().isoformat() + "Z"
            # 注意：此处不再 unload。主治医师(llama3.3)需在仍温时先清洗在档佐证料，
            # 之后由调用方显式 release() 释放显存（见 main.py 问诊结束分支）。
            return parsed, True

        if parsed and parsed.get("type") == "question":
            q = {
                "question": parsed.get("question", ""),
                "options": parsed.get("options", []) or [],
                "multi": bool(parsed.get("multi", False)),
                "allow_free_text": parsed.get("allow_free_text", True),
            }
            self.current_question = q["question"]
            self.current_options = list(q["options"])
            return q, False

        # 兜底：模型没按协议输出 JSON，则把整段当作自由文本问题，无选项
        self.current_question = response
        self.current_options = []
        return {"question": response, "options": [], "multi": False, "allow_free_text": True}, False

    async def curate_evidence(self, raw_evidence: str, chief_complaint: str) -> str:
        """主治医师(llama3.3，仍温)清洗在档报告：提炼与本次主诉相关的客观佐证，
        剔除病人ID/检查号/设备参数/重复项/无关内容。供归档进症状包、再交评估。"""
        system = (
            "你是主治医师，正在转诊前整理病历。从【既往在档报告】中，只挑出与患者本次主诉"
            "相关的【客观发现与关键数值】（如体积/大小/指标数值/明确诊断），剔除病人ID、检查号、"
            "设备参数、机构抬头、重复项与无关内容。用简洁中文条列，每条标明来源类型与日期。"
            "严禁臆测或新增报告中没有的信息。若无相关内容，只回复『无相关既往佐证』。"
        )
        user = (
            f"本次主诉：{chief_complaint or '（未明确）'}\n\n"
            f"【既往在档报告】\n{raw_evidence}\n\n相关客观佐证："
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        try:
            out = await oc.chat(MODEL, messages, options={"temperature": 0.2, "num_predict": 600})
            return out.strip()
        except Exception:
            return ""

    async def release(self) -> None:
        """释放 llama3.3 显存（keep_alive=0）。问诊+清洗完成后由调用方调用。"""
        try:
            await oc.generate(model=MODEL, prompt="unload", keep_alive=0)
        except Exception:
            pass

    async def more_options(self) -> list[str]:
        """需求：点"更多"时，为当前问题（主题不变）追加更多选项，不推进问诊。

        返回新增选项（不含已展示过的）。
        """
        if not self.current_question:
            return []
        existing = "、".join(self.current_options) or "（无）"
        prompt = (
            f"当前问题：{self.current_question}\n"
            f"已经提供过的选项：{existing}\n"
            "请为【同一个问题】再补充 6-10 个【新的、具体的、不重复】选项，"
            "覆盖前面没列到的常见可能。只输出一个 JSON，且只含 options 字段，"
            '形如 {"options":["...","..."]}，不要其他文字。'
        )
        resp = await oc.generate(
            model=MODEL,
            prompt=prompt,
            system="You output only one JSON object with an 'options' array of Simplified Chinese strings.",
            options={"temperature": 0.5},
        )
        parsed = _extract_json(resp)
        new = []
        if parsed and isinstance(parsed.get("options"), list):
            seen = set(self.current_options)
            for o in parsed["options"]:
                if isinstance(o, str) and o.strip() and o not in seen:
                    new.append(o.strip())
                    seen.add(o.strip())
        self.current_options.extend(new)
        return new
