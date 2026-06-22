"""
A1 问诊交互模块。
负责与用户进行多轮对话，收集结构化症状数据包。
"""

import json
from datetime import datetime
from . import ollama_client as oc

MODEL = "llama3.3:70b"

def build_system_prompt(department: str) -> str:
    return (
        f"You are a professional AI Medical Interviewer in the {department} department. Your goal is to collect a structured health profile from the patient. "
        "You must gather information for the following fields:\n"
        "1. Chief Complaint (主诉): The primary reason for the visit.\n"
        "2. Symptoms (详细症状): Detailed description of current symptoms, onset, and severity.\n"
        "3. Past Medical History (既往病史): Previous diagnoses, surgeries, or chronic conditions.\n"
        "4. Current Medications (当前用药): Any prescriptions, OTC drugs, or supplements currently taken.\n"
        "5. Family History (家族史): Relevant hereditary diseases in immediate family.\n\n"
        "Guidelines:\n"
        "- Be professional, empathetic, and concise.\n"
        "- Ask only ONE question at a time to avoid overwhelming the patient.\n"
        "- Do not provide medical advice or diagnosis; your role is purely data collection.\n"
        "- Conduct the interview in Chinese (Simplified).\n"
        "- When you have collected all necessary information, or the user indicates they are finished, "
        "you MUST output a final summary as a JSON object and nothing else. The JSON must follow this structure:\n"
        "{\n"
        "  \"chief_complaint\": \"...\",\n"
        "  \"symptoms\": \"...\",\n"
        "  \"history\": \"...\",\n"
        "  \"medications\": \"...\",\n"
        "  \"family_history\": \"...\",\n"
        f"  \"department\": \"{department}\"\n"
        "}"
    )

class MedicalInterviewer:
    """
    问诊状态机，管理多轮对话并产出结构化数据。
    """
    def __init__(self, user_id: str, department: str):
        self.user_id = user_id
        self.department = department
        self.history = []  # 存储格式: [{"role": "user", "content": "..."}, ...]

    async def chat(self, message: str) -> tuple[str | dict, bool]:
        """
        进行一轮对话。
        返回: (响应内容, 是否已完成收集)
        """
        # 更新历史
        self.history.append({"role": "user", "content": message})

        # 构造 Prompt (由于 ollama_client.generate 使用的是简单 prompt，我们将历史平铺)
        full_prompt = ""
        for turn in self.history:
            role = "Patient" if turn["role"] == "user" else "Interviewer"
            full_prompt += f"{role}: {turn['content']}\n"
        full_prompt += "Interviewer: "

        # 调用 LLM (中间轮次不传 keep_alive，模型常驻显存)
        response = await oc.generate(
            model=MODEL,
            prompt=full_prompt,
            system=build_system_prompt(self.department)
        )

        self.history.append({"role": "assistant", "content": response})

        # 检查是否产出 JSON (简单判定: 是否包含大括号且结构像JSON)
        cleaned_response = response.strip()
        if cleaned_response.startswith("{") and cleaned_response.endswith("}"):
            try:
                data = json.loads(cleaned_response)
                data["collected_at"] = datetime.utcnow().isoformat() + "Z"

                # 问诊结束，补发一次 keep_alive=0 请求以立即释放模型显存
                await oc.generate(model=MODEL, prompt="unload", keep_alive=0)

                return data, True
            except json.JSONDecodeError:
                pass

        return response, False
