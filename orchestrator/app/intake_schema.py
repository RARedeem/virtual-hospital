"""注册式采集表单 schema —— 由外挂 JSON 数据集驱动（与 interview-lab 同源）。

数据集在 `intake_datasets/*.json`：`_base.json`(共享骨架) + `<code>.json`(单科)。
本模块只做加载+组装：代码管流程（主诉→现病史→既往/用药/家族→各专科领域），
字段内容全来自数据集——不在代码里硬编码任何科室字段（见 no-hardcode 纪律）。
每次请求实时读盘，改 JSON 即生效。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = os.path.join(HERE, "intake_datasets")


def _load(name: str) -> dict:
    with open(os.path.join(DATASETS, name), encoding="utf-8") as f:
        return json.load(f)


def list_depts() -> list[dict]:
    """从 intake_datasets/ 发现科室（文件名即 code，_ 开头为公共件跳过），按 order 排序。"""
    out = []
    for fn in sorted(os.listdir(DATASETS)):
        if fn.endswith(".json") and not fn.startswith("_"):
            d = _load(fn)
            out.append({"code": d.get("code", fn[:-5]), "name": d.get("name", fn[:-5]),
                        "order": d.get("order", 99)})
    return sorted(out, key=lambda x: (x["order"], x["code"]))


def schema_for(dept_code: str) -> dict:
    """组装表单 schema：通用骨架(_base.json) + 该科数据集。代码只管流程，字段全来自 JSON。"""
    base = _load("_base.json")
    try:
        d = _load(f"{dept_code}.json")
    except FileNotFoundError:
        d = _load("urology.json")
    hpi_fields = list(base["common_hpi"]) + [
        {"key": "伴随症状", "label": "伴随表现（可多选）", "type": "multi", "options": d.get("assoc", [])}]
    dept_sections = [{"key": f"sp{i}", "title": s["title"], "fields": s["fields"]}
                     for i, s in enumerate(d.get("sections", []))]
    sections = [
        {"key": "chief", "title": base["chief"]["title"], "fields": [base["chief"]["field"]]},
        {"key": "hpi", "title": "现病史", "fields": hpi_fields},
    ] + base["history_sections"] + dept_sections
    return {"department": d.get("name", "全科"), "department_code": d.get("code", dept_code),
            "sections": sections}
