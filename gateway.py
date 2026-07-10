import os
import json
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from curl_cffi.requests import AsyncSession

app = FastAPI(title="OpenCode Gateway (Ultra Fast)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EXPECTED_API_KEY = os.environ.get('PROXY_API_KEY', '')

PROXIES = {
    "http": "http://proxy:proxypass888@127.0.0.1:7920",
    "https": "http://proxy:proxypass888@127.0.0.1:7920"
}

# 全局共享异步连接池
client_session = None

@app.on_event("startup")
async def startup_event():
    global client_session
    client_session = AsyncSession(impersonate="chrome", proxies=PROXIES)

@app.on_event("shutdown")
async def shutdown_event():
    global client_session
    if client_session:
        await client_session.close()

@app.post("/v1/chat/completions")
@app.post("/zen/v1/chat/completions")
async def proxy_chat(request: Request):
    if EXPECTED_API_KEY:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith(f'Bearer {EXPECTED_API_KEY}'):
            print(f"[Security] 拦截非法请求: 密钥错误 ({request.client.host})", flush=True)
            return Response(
                content=json.dumps({"error": {"message": "Invalid API Key. Please provide the correct Authorization: Bearer <key>.", "type": "AuthenticationError"}}),
                status_code=401,
                media_type="application/json"
            )

    try:
        data = await request.json()
    except Exception:
        data = {}

    original_model = data.get('model', 'mimo-v2.5-pro')
    
    # 核心逆向逻辑 1: 强行将模型名替换为官方免费通道支持的模型
    data['model'] = 'mimo-v2.5-free'
    is_stream = data.get('stream', False)

    # 核心逆向逻辑 2: 注入逆向提取出的鉴权 Token 和客户端标识，伪装 Chrome JA3 指纹
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer public',
        'x-opencode-client': 'desktop',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': request.headers.get('Accept', 'application/json')
    }

    try:
        resp = await client_session.post(
            'https://opencode.ai/zen/v1/chat/completions',
            json=data,
            headers=headers,
            stream=is_stream,
            timeout=180
        )

        if is_stream:
            # SSE 异步流式转发
            async def generate():
                try:
                    async for chunk in resp.aiter_content():
                        if chunk:
                            yield chunk.replace(b'mimo-v2.5-free', original_model.encode('utf-8'))
                finally:
                    pass # aiter_content reads to end
            
            return StreamingResponse(
                generate(), 
                status_code=resp.status_code, 
                media_type=resp.headers.get('Content-Type', 'text/event-stream')
            )
        else:
            # 非流式直接等待响应
            modified_content = resp.content.replace(b'mimo-v2.5-free', original_model.encode('utf-8'))
            return Response(content=modified_content, status_code=resp.status_code, media_type="application/json")
    
    except Exception as e:
        print(f"[Gateway] 转发失败: {e}", flush=True)
        return Response(
            content=json.dumps({"error": str(e)}),
            status_code=500,
            media_type="application/json"
        )

@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [{"id": "mimo-v2.5-pro", "object": "model", "created": 1686935002, "owned_by": "opencode"}]
    }

if __name__ == '__main__':
    print("========================================", flush=True)
    print("  极速 Python 异步网关启动！(监听端口: 8080)", flush=True)
    print("========================================", flush=True)
    # 使用 Uvicorn 提供极速 ASGI 服务
    uvicorn.run("gateway:app", host="0.0.0.0", port=8080, workers=1, access_log=False)
