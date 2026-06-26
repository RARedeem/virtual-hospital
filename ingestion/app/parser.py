"""
PDF 结构化解析与章节切片。

使用 Docling（IBM，约束 A 合规）解析 PDF 为带层级的文档树，
再按标题结构切片，保留语义完整性。超长章节二次切分。
"""
from dataclasses import dataclass, field

# 单 chunk 字符上限。超过则在段落边界二次切分。
# nomic-embed-text native context = 2048 tokens; ~3.5 chars/token → 7000 chars safe ceiling.
MAX_CHUNK_CHARS = 1800
# 二次切分时相邻 chunk 的重叠字符数，保留上下文连续性。
OVERLAP_CHARS = 200
# Hard ceiling — chunks exceeding this are force-split at word boundaries.
_HARD_LIMIT = 7000


@dataclass
class Chunk:
    text: str
    section: str                      # 所属章节标题路径，如 "Diagnosis > Criteria"
    order: int                        # 文档内顺序
    meta: dict = field(default_factory=dict)


def _split_at_words(text: str, section: str, start_order: int) -> list[Chunk]:
    """按词边界切分超过 _HARD_LIMIT 的段落，带重叠。"""
    chunks: list[Chunk] = []
    words = text.split()
    buf = ""
    order = start_order
    for word in words:
        candidate = (buf + " " + word) if buf else word
        if len(candidate) > MAX_CHUNK_CHARS and buf:
            chunks.append(Chunk(text=buf.strip(), section=section, order=order))
            order += 1
            tail = buf[-OVERLAP_CHARS:].split(" ", 1)[-1] if OVERLAP_CHARS else ""
            buf = (tail + " " + word) if tail else word
        else:
            buf = candidate
    if buf.strip():
        chunks.append(Chunk(text=buf.strip(), section=section, order=order))
    return chunks


def _split_long(text: str, section: str, start_order: int) -> list[Chunk]:
    """对超过上限的章节按段落边界二次切分，带重叠。超长段落再按词边界切分。"""
    if len(text) <= MAX_CHUNK_CHARS:
        return [Chunk(text=text.strip(), section=section, order=start_order)]

    chunks: list[Chunk] = []
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    buf = ""
    order = start_order
    for para in paragraphs:
        # Paragraph itself exceeds hard limit — flush buffer then word-split it
        if len(para) > _HARD_LIMIT:
            if buf.strip():
                chunks.append(Chunk(text=buf.strip(), section=section, order=order))
                order += 1
                buf = ""
            sub = _split_at_words(para, section, order)
            chunks.extend(sub)
            order += len(sub)
            continue
        if len(buf) + len(para) > MAX_CHUNK_CHARS and buf:
            chunks.append(Chunk(text=buf.strip(), section=section, order=order))
            order += 1
            # 重叠：保留上一 chunk 尾部
            buf = buf[-OVERLAP_CHARS:] + "\n\n" + para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf.strip():
        chunks.append(Chunk(text=buf.strip(), section=section, order=order))
    return chunks


def parse_and_chunk(pdf_path: str) -> list[Chunk]:
    """
    解析 PDF 并按章节结构切片。

    使用 Docling 结构化解析，按真实章节标题切片。
    返回有序 Chunk 列表，每个 chunk 携带其章节标题路径。

    指南语料均为原生数字 PDF（自带文本层），显式关闭 OCR：
      1) 无需 OCR——文本层直读即得，更快更准；
      2) 规避 Docling 默认 OCR 引擎（RapidOCR PP-OCRv6）在当前镜像不受支持而崩溃。
    扫描件指南若日后出现，再按需引入 OCR 开关。
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions(do_ocr=False)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(pdf_path)
    doc = result.document

    chunks: list[Chunk] = []
    current_section = "Preamble"
    section_buffer = ""
    order = 0

    # 遍历文档结构项，遇标题切换章节，累积正文
    for item, _level in doc.iterate_items():
        item_type = type(item).__name__

        if "Heading" in item_type or "Title" in item_type or "SectionHeader" in item_type:
            heading_text = getattr(item, "text", "").strip()
            # Skip URL-titled headings (reference list entries misclassified as headings)
            if heading_text.startswith("http://") or heading_text.startswith("https://"):
                if heading_text:
                    section_buffer += heading_text + "\n\n"
                continue
            # 遇新标题，先冲刷已累积的上一章节
            if section_buffer.strip():
                produced = _split_long(section_buffer, current_section, order)
                chunks.extend(produced)
                order += len(produced)
            current_section = heading_text or "Section"
            section_buffer = ""
        else:
            text = getattr(item, "text", "")
            if text:
                section_buffer += text + "\n\n"

    # 冲刷最后一个章节
    if section_buffer.strip():
        chunks.extend(_split_long(section_buffer, current_section, order))

    return chunks
