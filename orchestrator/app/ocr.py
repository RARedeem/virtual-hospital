"""上传报告的 OCR 文字提取 —— 用 glm-ocr 视觉模型。

⚠ 约束 A 红线：glm-ocr（GLM=北京智谱，属约束 A 黑名单机构）仅可用于此
**非评估链路**的本地 OCR 预处理，严禁接入 A1/A2/B 推理或翻译/检索任何评估环节。
（裁定与红线见 TODO-NEXT「约束 A 治理项」。）

PDF 先栅格化为图片（pdf2image + poppler），再逐页送 glm-ocr。
glm-ocr 小模型尾部会无限重复代码围栏，故用 stop=["```"] 从生成侧截断。
"""
import base64
import io

from . import ollama_client as oc

OCR_MODEL = "glm-ocr"
_OCR_PROMPT = "识别图片中的所有文字，原样输出，保留数值与单位，不要解释、不要翻译。"
_OCR_OPTIONS = {"temperature": 0, "num_predict": 1536, "stop": ["```"]}
_MAX_PDF_PAGES = 12


async def _ocr_image_bytes(img_bytes: bytes) -> str:
    b64 = base64.b64encode(img_bytes).decode()
    text = await oc.generate(
        model=OCR_MODEL, prompt=_OCR_PROMPT, images=[b64], options=_OCR_OPTIONS,
    )
    return text.strip().strip("`").strip()


def _pdf_to_png_pages(pdf_bytes: bytes) -> list[bytes]:
    """PDF → 每页 PNG 字节。依赖 pdf2image + poppler-utils。"""
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(pdf_bytes, dpi=200, fmt="png")[:_MAX_PDF_PAGES]
    out = []
    for page in pages:
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


async def extract_text(data: bytes, content_type: str) -> str:
    """对上传文件做 OCR，返回中文文本。content_type 决定走图片还是 PDF 分支。"""
    ct = (content_type or "").lower()
    if "pdf" in ct:
        pages = _pdf_to_png_pages(data)
        parts = []
        for i, png in enumerate(pages, 1):
            page_text = await _ocr_image_bytes(png)
            if page_text:
                parts.append(f"【第{i}页】\n{page_text}")
        return "\n\n".join(parts)
    # 图片：jpeg / png 直接送
    return await _ocr_image_bytes(data)
