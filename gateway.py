import os
import json
from curl_cffi import requests
from flask import Flask, request, Response, stream_with_context
import logging

# 关闭 Flask 默认在控制台疯狂输出访问日志，保持代理引擎日志干净
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# 获取系统的专属访问密钥 (如果未配置，则开放裸奔，但脚本现在会强制配置)
EXPECTED_API_KEY = os.environ.get('PROXY_API_KEY', '')

# 底层控制器 HTTP 代理端口 (兼容 curl_cffi 模拟 Chrome)
PROXIES = {
    "http": "http://proxy:proxypass888@127.0.0.1:7920",
    "https": "http://proxy:proxypass888@127.0.0.1:7920"
}

@app.route('/v1/chat/completions', methods=['POST'])
@app.route('/zen/v1/chat/completions', methods=['POST'])
def proxy_chat():
    try:
        # ========================================================
        # 安全防御: API 访问鉴权
        # ========================================================
        if EXPECTED_API_KEY:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith(f'Bearer {EXPECTED_API_KEY}'):
                print(f"[Security] 拦截到非法请求: 密钥错误 ({request.remote_addr})", flush=True)
                return {"error": {"message": "Invalid API Key. Please provide the correct Authorization: Bearer <key>.", "type": "AuthenticationError"}}, 401

        data = request.get_json(silent=True) or {}
        original_model = data.get('model', 'mimo-v2.5-pro')
        
        # 核心逆向逻辑 1: 强行将模型名替换为官方免费通道支持的模型
        data['model'] = 'mimo-v2.5-free'
        
        is_stream = data.get('stream', False)

        # 核心逆向逻辑 2: 注入逆向提取出的鉴权 Token 和客户端标识，以及浏览器 UA 防拦截
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer public',
            'x-opencode-client': 'desktop',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': request.headers.get('Accept', 'application/json')
        }

        print("[Gateway] 收到客户端请求，正在通过底层无缝隧道转发...", flush=True)
        # 将请求透传给官方接口，通过本地 SOCKS5 发送
        resp = requests.post(
            'https://opencode.ai/zen/v1/chat/completions',
            json=data,
            headers=headers,
            proxies=PROXIES,
            stream=is_stream,
            impersonate="chrome"
        )

        if is_stream:
            # 核心逆向逻辑 3: SSE 流式数据替换模型名后抛回给客户端
            def generate():
                for chunk in resp.iter_content(chunk_size=None):
                    if chunk:
                        # 核心逆向逻辑 4: 伪装返回值里的模型名称
                        chunk = chunk.replace(b'mimo-v2.5-free', original_model.encode('utf-8'))
                        yield chunk

            return Response(
                stream_with_context(generate()), 
                status=resp.status_code, 
                headers={'Content-Type': resp.headers.get('Content-Type', 'text/event-stream')}
            )
        else:
            # 非流式请求，直接替换 JSON
            try:
                resp_json = resp.json()
                if 'model' in resp_json:
                    resp_json['model'] = original_model
                return resp_json, resp.status_code
            except:
                modified_content = resp.content.replace(b'mimo-v2.5-free', original_model.encode('utf-8'))
                return Response(modified_content, status=resp.status_code, headers={'Content-Type': 'application/json'})
    except Exception as e:
        print(f"[Gateway] 转发失败: {e}", flush=True)
        return {"error": str(e)}, 500

@app.route('/v1/models', methods=['GET'])
def models():
    # 返回一个兼容 OpenAI 的假模型列表
    return {
        "object": "list",
        "data": [{"id": "mimo-v2.5-pro", "object": "model", "created": 1686935002, "owned_by": "opencode"}]
    }

if __name__ == '__main__':
    print("========================================", flush=True)
    print("  Python AI 逆向网关启动！(监听端口: 8080)", flush=True)
    print("========================================", flush=True)
    app.run(host='0.0.0.0', port=8080, threaded=True)
