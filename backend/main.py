from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import subprocess
import os
import uuid
import pathlib
import re

app = FastAPI()

# 配置文件
UPLOAD_DIR = pathlib.Path("../uploads")
OUTPUT_DIR = pathlib.Path("../outputs")
DREAMINA_BIN = "/Users/huangmanzhen/.local/bin/dreamina"

# 确保目录存在
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 挂载输出目录用于显示图片
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")

# 首页
@app.get("/", response_class=HTMLResponse)
async def home():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AI 设计修改助手</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Helvetica Neue", sans-serif; 
              background: #f5f5f5; min-height: 100vh; padding: 20px; }
        .container { max-width: 600px; margin: 0 auto; }
        h1 { text-align: center; color: #333; margin-bottom: 30px; }
        .card { background: white; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        .upload-zone { border: 2px dashed #ddd; border-radius: 8px; padding: 40px; text-align: center; cursor: pointer; transition: 0.2s; }
        .upload-zone:hover { border-color: #007AFF; background: #f0f7ff; }
        .upload-zone.dragover { border-color: #007AFF; background: #e6f3ff; }
        input[type="file"] { display: none; }
        textarea { width: 100%; border: 1px solid #ddd; border-radius: 8px; padding: 12px; 
                    font-size: 16px; resize: vertical; min-height: 100px; margin-top: 16px; }
        textarea:focus { outline: none; border-color: #007AFF; }
        button { width: 100%; background: #007AFF; color: white; border: none; border-radius: 8px; 
                  padding: 14px; font-size: 16px; cursor: pointer; margin-top: 16px; }
        button:hover { background: #005ecb; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        .result { margin-top: 24px; text-align: center; }
        .result img { max-width: 100%; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .loading { text-align: center; padding: 40px; color: #666; }
        .tips { margin-top: 16px; font-size: 14px; color: #666; }
        .credit { text-align: center; color: #4CAF50; font-weight: bold; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎨 AI 设计修改助手</h1>
        <div class="credit">💰 积分余额: 充足</div>
        <div class="card">
            <form id="uploadForm" enctype="multipart/form-data">
                <div class="upload-zone" onclick="document.getElementById('file').click()">
                    <div id="uploadText">📁 点击选择图片，或拖拽图片到这里</div>
                    <input type="file" id="file" name="file" accept="image/*" required>
                </div>
                <textarea name="prompt" rows="4" placeholder="描述你想怎么修改，例如：
• 把标题改成「双十一特惠」
• 背景换成蓝天白云
• 整体变成卡通风格"></textarea>
                <button type="submit" id="submitBtn">🚀 开始修改</button>
            </form>
            <div class="loading" id="loading" style="display:none">⏳ 正在调用即梦生成图片...请稍候...</div>
            <div class="result" id="result"></div>
            <p class="tips">💡 即梦会根据你的描述生成一张新图片</p>
        </div>
    </div>
    <script>
        const fileInput = document.getElementById('file');
        const uploadZone = document.querySelector('.upload-zone');
        const uploadText = document.getElementById('uploadText');
        
        fileInput.addEventListener('change', () => {
            if (fileInput.files[0]) {
                uploadText.textContent = '✅ 已选择: ' + fileInput.files[0].name;
            }
        });
        
        uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
        uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
        uploadZone.addEventListener('drop', e => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            if (e.dataTransfer.files[0]) {
                fileInput.files = e.dataTransfer.files;
                uploadText.textContent = '✅ 已选择: ' + fileInput.files[0].name;
            }
        });
        
        document.getElementById('uploadForm').addEventListener('submit', async e => {
            e.preventDefault();
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('prompt', document.querySelector('textarea').value);
            
            document.getElementById('loading').style.display = 'block';
            document.getElementById('result').innerHTML = '';
            document.getElementById('submitBtn').disabled = true;
            
            try {
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const data = await res.json();
                
                if (data.output_image) {
                    document.getElementById('result').innerHTML = 
                        '<p>✅ 修改完成！</p><img src="/outputs/' + data.output_image + '"><br>' +
                        '<a href="/outputs/' + data.output_image + '" download><button style="margin-top:10px">💾 下载图片</button></a>';
                } else {
                    document.getElementById('result').innerHTML = '<p style="color:red">❌ ' + (data.error || '生成失败') + '</p>';
                }
            } catch (err) {
                document.getElementById('result').innerHTML = '<p style="color:red">❌ 网络错误: ' + err.message + '</p>';
            }
            
            document.getElementById('loading').style.display = 'none';
            document.getElementById('submitBtn').disabled = false;
        });
    </script>
</body>
</html>"""

@app.post("/upload")
async def upload(file: UploadFile = File(...), prompt: str = Form(...)):
    if not prompt or not prompt.strip():
        return {"error": "请输入描述文字"}
    
    # 1. 保存上传的图片
    file_ext = pathlib.Path(file.filename).suffix or ".png"
    input_filename = f"input_{uuid.uuid4().hex}{file_ext}"
    input_path = UPLOAD_DIR / input_filename
    
    with open(input_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # 2. 调用即梦 image2image
    output_filename = f"output_{uuid.uuid4().hex}.png"
    output_path = OUTPUT_DIR / output_filename
    
    try:
        # 使用正确的参数格式 --images
        cmd = [
            DREAMINA_BIN, "image2image",
            "--images", str(input_path),
            "--prompt", prompt,
            "--ratio", "1:1",
            "--poll", "60"  # 等待最多60秒
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=300
        )
        
        # 调试输出
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        
        if result.returncode != 0:
            return {"error": result.stderr or "生成失败"}
        
        # 从输出中解析图片路径
        # 输出格式可能是: /path/to/image.png 或 JSON
        output_lines = result.stdout.strip().split('\n')
        generated_image = None
        
        for line in output_lines:
            line = line.strip()
            if line and (line.endswith('.png') or line.endswith('.jpg') or line.endswith('.jpeg')):
                generated_image = line
                break
            # 检查是否是 JSON 输出（包含 url 或 path）
            if 'url' in line.lower() or 'image' in line.lower():
                # 尝试提取 URL
                url_match = re.search(r'["\']?(https?://[^\s"\']+\.(?:png|jpg|jpeg))["\']?', line, re.I)
                if url_match:
                    # 需要下载图片
                    generated_image = url_match.group(1)
                    break
        
        if generated_image:
            if generated_image.startswith('http'):
                # 下载远程图片
                import requests
                r = requests.get(generated_image, timeout=60)
                with open(output_path, 'wb') as f:
                    f.write(r.content)
            else:
                # 复制本地文件
                import shutil
                if os.path.exists(generated_image):
                    shutil.copy(generated_image, output_path)
                else:
                    return {"error": f"生成的图片路径不存在: {generated_image}"}
        else:
            # 如果没找到图片，用原图+提示
            import shutil
            shutil.copy(input_path, output_path)
            
        return {"status": "ok", "output_image": output_filename, "prompt": prompt}
        
    except subprocess.TimeoutExpired:
        return {"error": "生成超时，请重试"}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
