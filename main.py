"""
历史人物封面生成 Web 服务（扁平结构版本）
FastAPI 后端 — 转发用户自填的火山引擎 API 凭证，调用豆包文案+生图 API
部署：Render / 腾讯云 CloudBase / Cloud Run / CVM
"""
import json
import time
import base64
import urllib.request
import urllib.error
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

app = FastAPI(title="历史人物封面生成器", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 前端静态文件目录（扁平结构：main.py 和 index.html 同级）
FRONTEND_DIR = Path(__file__).resolve().parent

# 图片输出目录
OUTPUT_DIR = Path(__file__).resolve().parent / "generated"

# ── 火山引擎 API 地址 ──
CHAT_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
IMAGE_API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

# ── 文案生成的系统提示词 ──
SYSTEM_PROMPT = """你是一位精通中国历史文化、AI绘画和短视频封面设计的创意总监。用户会给你一个历史人物的名字（如帝王、将相、才女、文人等），你需要输出以下四项内容，严格按格式返回：

1. 封面大标题（title）：2-6个字的震撼标题，突出人物最核心的历史标签/生平概括。这个标题会直接作为封面第一行文字显示。
2. 单行人物简介（intro）：一句话（15-30字）概括此人最显著的历史功绩或特征。
3. 标签（tags）：3-5个热门短视频风格的标签，用逗号分隔。包含人物朝代、身份、代表性成就或网络热词风格标签（如 #唐朝 #诗人 #浪漫主义 #千古诗仙 #酒仙）。

4. 英文国风9:16竖版绘画提示词（prompt）：一段详细的英文prompt，用于生成中国传统国画风格的9:16竖版人物肖像图。**注意：图片上不要渲染任何文字**，标题文字将由前端后期叠加。

【画面基础】
- 必须包含 "Chinese traditional ink painting style" 或 "Chinese classical painting style"
- 必须包含 "9:16 vertical portrait"
- 描绘人物标志性外貌、服饰、场景元素
- 包含国风美学关键词（如 mist, silk robes, golden light, calligraphy, bamboo, mountains 等）
- 画面整体色调偏暗或有层次感，为后期叠加文字留足对比度
- **画面中心区域保持相对干净**，避免复杂元素，方便后期叠加标题文字

【重要：不要包含任何文字渲染指令】
不要要求AI在图片上渲染任何文字、标题、书法字迹等。图片应该是纯净的人物肖像背景图，标题将由前端程序叠加到图片上。

【prompt结构参考模板】
Chinese traditional ink painting style, 9:16 vertical portrait of [人物描述], [场景细节], [国风元素], mysterious atmosphere, elegant composition, center area kept relatively clean for overlay...

严格按以下JSON格式输出，不要输出任何其他内容：
{"title": "封面大标题", "intro": "人物简介", "tags": "标签1,标签2,标签3", "prompt": "完整英文绘画提示词（不含文字渲染指令）"}"""

# ── 请求模型 ──
class TextGenRequest(BaseModel):
    figure_name: str
    api_key: str
    endpoint_id: str

class ImageGenRequest(BaseModel):
    figure_name: str = ""
    prompt: str
    api_key: str
    endpoint_id: str
    n: int = 4  # 生成数量，默认4张

# ── 辅助函数 ──
def call_chat_api(figure_name: str, api_key: str, endpoint_id: str) -> dict:
    """调用火山引擎对话API生成文案"""
    payload = json.dumps({
        "model": endpoint_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": figure_name}
        ],
        "temperature": 0.8,
        "max_tokens": 1024
    }).encode("utf-8")

    req = urllib.request.Request(
        CHAT_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"火山引擎API错误 ({e.code}): {err_body}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"网络错误: {e.reason}")

    content = body["choices"][0]["message"]["content"].strip()

    # 处理markdown代码块包裹
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    # 解析JSON
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            result = json.loads(content[start:end])
        else:
            raise HTTPException(status_code=502, detail=f"无法解析API返回: {content[:200]}")

    return result


def call_image_api(prompt: str, api_key: str, endpoint_id: str, n: int) -> list:
    """调用火山引擎图像生成API，返回图片URL列表"""
    payload = json.dumps({
        "model": endpoint_id,
        "prompt": prompt,
        "size": "1440x2560",  # 9:16竖版，API最低像素要求
        "n": n,
        "response_format": "url"
    }).encode("utf-8")

    req = urllib.request.Request(
        IMAGE_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"火山引擎API错误 ({e.code}): {err_body}")
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"网络错误: {e.reason}")

    urls = [item["url"] for item in body.get("data", [])]
    return urls


def download_image_bytes(url: str) -> bytes:
    """下载图片返回字节数据"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


# ── API 路由 ──
@app.get("/")
@app.get("/index.html")
def serve_frontend():
    """提供前端页面"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"service": "历史人物封面生成器", "version": "1.0.0", "status": "running"}

@app.get("/api/health")
def health():
    return {"service": "历史人物封面生成器", "version": "1.0.0", "status": "running"}


@app.post("/api/generate-text")
def generate_text(req: TextGenRequest):
    """
    生成封面文案
    用户自填: figure_name, api_key, endpoint_id
    """
    try:
        result = call_chat_api(req.figure_name, req.api_key, req.endpoint_id)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-images")
def generate_images(req: ImageGenRequest):
    """
    生成封面图片
    用户自填: prompt, api_key, endpoint_id, n(可选)
    返回: base64编码的图片列表
    """
    all_urls = []

    # 首次批量请求
    try:
        urls = call_image_api(req.prompt, req.api_key, req.endpoint_id, req.n)
        all_urls.extend(urls)
    except HTTPException:
        # 批量失败则逐张生成
        all_urls = []

    # 不足则逐张补齐
    if len(all_urls) < req.n:
        remaining = req.n - len(all_urls)
        for i in range(remaining):
            try:
                urls = call_image_api(req.prompt, req.api_key, req.endpoint_id, 1)
                all_urls.extend(urls)
                time.sleep(1)
            except Exception:
                pass

    if not all_urls:
        raise HTTPException(status_code=502, detail="未能生成任何图片")

    # 下载、存盘、转base64
    # 创建输出文件夹：generated/{人物名}_{时间戳}/
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = req.figure_name.strip() or "cover"
    folder_name = f"{safe_name}_{ts}"
    img_dir = OUTPUT_DIR / folder_name
    img_dir.mkdir(parents=True, exist_ok=True)

    images_base64 = []
    for idx, url in enumerate(all_urls):
        try:
            img_bytes = download_image_bytes(url)
            # 保存到本地
            filename = f"{safe_name}_{idx + 1}.jpg"
            filepath = img_dir / filename
            filepath.write_bytes(img_bytes)
            # 转base64给前端展示
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            images_base64.append({
                "index": idx + 1,
                "base64": b64,
                "size_kb": round(len(img_bytes) / 1024, 1),
                "filename": filename
            })
        except Exception as e:
            images_base64.append({
                "index": idx + 1,
                "error": str(e)
            })

    return {
        "success": True,
        "total": len(images_base64),
        "images": images_base64,
        "folder": str(img_dir)
    }


@app.get("/api/download-image")
def download_image_proxy(url: str):
    """代理下载图片（用于前端下载按钮）"""
    try:
        img_bytes = download_image_bytes(url)
        return Response(
            content=img_bytes,
            media_type="image/jpeg",
            headers={"Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename=cover.jpg"}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/download-zip")
def download_zip(folder: str):
    """将指定文件夹打包为 zip 下载"""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="文件夹不存在或已清理")

    # 在内存中创建 zip
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(folder_path.iterdir()):
            if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                zf.write(f, f.name)
    buf.seek(0)

    zip_name = folder_path.name + ".zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'}
    )
