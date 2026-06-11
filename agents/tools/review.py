"""
审稿工具：review_paper / visual_review

封装 lib/perform_llm_review.py 和 perform_vlm_review.py。
导师用来审稿，研究生用来自查。
"""
import os
import json
from pathlib import Path

from agents.tool import Tool, ToolResult
from agents.context import SharedContext


class ReviewPaper(Tool):
    name = "review_paper"
    description = "用 LLM 审稿（论文内容审查），返回评分和建议（调用 lib/perform_llm_review.py）"
    parameters = {
        "type": "object",
        "properties": {
            "exp_dir": {"type": "string", "description": "实验目录路径（含 PDF）"},
            "model": {"type": "string", "description": "审稿模型，留空用默认"},
            "num_reviewers": {"type": "integer", "description": "审稿人数量，默认3"},
        },
        "required": ["exp_dir"],
    }
    permission = "review:write"
    confidence_required = 0.3

    async def execute(self, ctx: SharedContext, *, exp_dir: str,
                      model: str = "", num_reviewers: int = 3) -> ToolResult:
        exp_path = Path(exp_dir)
        if not exp_path.is_absolute():
            exp_path = ctx.root / exp_dir
        if not exp_path.exists():
            return ToolResult(success=False, error=f"Experiment dir not found: {exp_dir}")

        try:
            from lib.llm_review import load_paper
            pdf_path = None
            for f in exp_path.rglob("*.pdf"):
                pdf_path = f
                break
            if not pdf_path:
                return ToolResult(success=False, error="No PDF found in experiment dir")

            if not model:
                model = ctx.get_model_config()["model"]

            from api import create_client
            client, actual_model, original_model = create_client(model)

            mc = ctx.get_model_config()

            from lib.llm import get_response_from_llm, extract_json_between_markers

            from lib.llm_review import (
                reviewer_system_prompt_neg,
                reviewer_system_prompt_pos,
                template_instructions,
                review_form,
            )
            from pypdf import PdfReader

            text = load_paper(str(pdf_path))
            if not text:
                return ToolResult(success=False, error="Failed to extract text from PDF")

            reviews = []
            for i in range(num_reviewers):
                sys_prompt = reviewer_system_prompt_neg if i % 2 == 0 else reviewer_system_prompt_pos
                full_prompt = sys_prompt + template_instructions + review_form + "\n\n" + text[:50000]
                response, _ = get_response_from_llm(
                    prompt=full_prompt,
                    client=client,
                    model=actual_model,
                    system_message="You are an expert academic paper reviewer.",
                    temperature=mc.get("temperature", 0.5),
                    max_tokens=mc.get("max_tokens", 16384),
                )
                review_json = extract_json_between_markers(response)
                reviews.append({
                    "reviewer_id": i + 1,
                    "raw_response": response[:2000],
                    "parsed": review_json,
                })

            return ToolResult(success=True, data={
                "pdf_path": str(pdf_path),
                "reviews": reviews,
                "num_reviewers": len(reviews),
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class AnalyzeImages(Tool):
    name = "analyze_images"
    description = (
        "用 VLM 分析图片，返回文字描述。"
        "可分析实验图表、论文截图、PDF 中的图表等。"
        "用法: analyze_images(image_paths=['figures/fig1.png'], question='这个图表显示了什么？')"
    )
    parameters = {
        "type": "object",
        "properties": {
            "image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "图片文件路径列表（支持 png/jpg/pdf）",
            },
            "question": {
                "type": "string",
                "description": "要对图片提出的问题，如'分析这个实验图表的指标趋势'",
            },
        },
        "required": ["image_paths", "question"],
    }
    permission = "research:read"
    confidence_required = 0.0

    async def execute(self, ctx: SharedContext, *, image_paths: list,
                      question: str) -> ToolResult:
        try:
            from api import create_client
            client, actual_model, _ = create_client("custom/mimo-v2.5")
        except Exception as e:
            return ToolResult(success=False, error=f"VLM client init failed: {e}")

        resolved = []
        for p in image_paths:
            pp = Path(p)
            if not pp.is_absolute():
                pp = ctx.root / p
            if pp.suffix.lower() == ".pdf":
                pdf_imgs = self._extract_pdf_images(pp)
                resolved.extend(pdf_imgs)
            elif pp.exists():
                resolved.append(str(pp))
            else:
                for f in pp.parent.rglob(f"{pp.stem}*"):
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                        resolved.append(str(f))
                        break

        if not resolved:
            return ToolResult(success=False, error=f"No images found: {image_paths}")

        try:
            from lib.vlm import get_response_from_vlm
            response, _ = get_response_from_vlm(
                msg=question,
                image_paths=resolved[:10],
                client=client,
                model=actual_model,
                system_message="You are a scientific figure analyst. Describe what you see precisely and concisely.",
                temperature=0.3,
            )
            return ToolResult(success=True, data={
                "images_analyzed": len(resolved[:10]),
                "image_paths": resolved[:10],
                "analysis": response or "VLM returned empty",
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _extract_pdf_images(self, pdf_path: Path) -> list[str]:
        import tempfile, pymupdf
        out = []
        try:
            doc = pymupdf.open(str(pdf_path))
            tmp = tempfile.mkdtemp()
            for page_num in range(min(len(doc), 20)):
                page = doc[page_num]
                for img_idx, img_info in enumerate(page.get_images(full=True)):
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        if len(img_bytes) < 500:
                            continue
                        ext = base_image.get("ext", "png")
                        fpath = os.path.join(tmp, f"p{page_num}_img{img_idx}.{ext}")
                        with open(fpath, "wb") as f:
                            f.write(img_bytes)
                        out.append(fpath)
                    except Exception:
                        continue
        except Exception:
            pass
        return out[:20]


class VisualReview(Tool):
    name = "visual_review"
    description = "用 VLM 审查论文中的图表（图片与标题一致性审查，调用 lib/perform_vlm_review.py）"
    parameters = {
        "type": "object",
        "properties": {
            "exp_dir": {"type": "string", "description": "实验目录路径（含 PDF）"},
            "model": {"type": "string", "description": "VLM 模型，留空用默认 VLM"},
        },
        "required": ["exp_dir"],
    }
    permission = "review:write"
    confidence_required = 0.3

    async def execute(self, ctx: SharedContext, *, exp_dir: str,
                      model: str = "") -> ToolResult:
        exp_path = Path(exp_dir)
        if not exp_path.is_absolute():
            exp_path = ctx.root / exp_dir
        if not exp_path.exists():
            return ToolResult(success=False, error=f"Experiment dir not found: {exp_dir}")

        try:
            import pymupdf
            import base64

            pdf_path = None
            for f in exp_path.rglob("*.pdf"):
                pdf_path = f
                break
            if not pdf_path:
                return ToolResult(success=False, error="No PDF found")

            doc = pymupdf.open(str(pdf_path))
            images = []
            for page_num in range(min(len(doc), 20)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                for img_idx, img_info in enumerate(image_list):
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        img_bytes = base_image["image"]
                        if len(img_bytes) < 500:
                            continue
                        b64 = base64.b64encode(img_bytes).decode("utf-8")
                        images.append({
                            "page": page_num + 1,
                            "index": img_idx,
                            "size": len(img_bytes),
                            "base64_preview": b64[:100] + "...",
                        })
                    except Exception:
                        continue

            return ToolResult(success=True, data={
                "pdf_path": str(pdf_path),
                "total_pages": len(doc),
                "images_found": len(images),
                "images": images[:20],
                "note": "Image data extracted. Use VLM to analyze specific images.",
            })
        except Exception as e:
            return ToolResult(success=False, error=str(e))
