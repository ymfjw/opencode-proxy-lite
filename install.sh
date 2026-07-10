#!/bin/bash
set -e

# =================================================================
# OpenCode 高并发代理网关 (Docker + IP轮换 极致速度版) 一键部署脚本
# 仅支持 Ubuntu/Debian 系统 (建议 Ubuntu 22.04+)
# =================================================================

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo "[-] 请使用 root 权限运行此脚本 (sudo bash install_docker_proxy.sh)"
  exit 1
fi

echo "[*] ========================================"
echo "[*] 开始自动化部署 OpenCode 高级代理网关 (Docker + IP轮换版)..."
echo "[*] ========================================"

# 1. 安装系统级依赖
echo "[*] 正在安装底层系统依赖 (docker, openvpn, vnstat, python3, venv)..."
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get update -yq
apt-get install -yq docker.io docker-compose-v2 openvpn vnstat python3 python3-pip python3-venv curl

# 2. 准备目录并从 GitHub 拉取最新代码
echo "[*] 正在创建工作目录并从 GitHub 拉取最新项目文件..."
mkdir -p /opt/proxy_lite

REPO_URL="https://github.com/ymfjw/opencode-proxy-lite.git"
rm -rf /tmp/opencode_repo
git clone "$REPO_URL" /tmp/opencode_repo
cp -r /tmp/opencode_repo/* /opt/proxy_lite/
chmod +x /opt/proxy_lite/*.py 2>/dev/null || true
# 安装流量监控工具
cp /opt/proxy_lite/traffic.sh /usr/local/bin/traffic 2>/dev/null || true
chmod +x /usr/local/bin/traffic 2>/dev/null || true
rm -rf /tmp/opencode_repo
echo "[+] 项目文件已更新至最新版本"

# 3. 安装 traffic 监控脚本
if [ -f "traffic.sh" ]; then
    cp traffic.sh /usr/local/bin/traffic
    chmod +x /usr/local/bin/traffic
    echo "[+] 流量监控工具已安装"
else
    echo "[!] 警告: 未找到 traffic.sh，跳过监控工具安装。"
fi

# 4. 配置 Python 独立虚拟环境 (隔离系统包，用于 Lite Manager)
echo "[*] 3. 初始化 Python 虚拟环境并安装核心依赖 (极速异步网关)..."
python3 -m venv /opt/proxy_lite/venv
/opt/proxy_lite/venv/bin/pip install --upgrade pip
/opt/proxy_lite/venv/bin/pip install requests schedule flask curl_cffi fastapi uvicorn

# 5. 配置 Lite Manager 系统服务
echo "[*] 正在配置开机自启系统服务 lite-manager.service..."
cat << EOF > /etc/systemd/system/lite-manager.service
[Unit]
Description=OpenCode Proxy Lite Manager (Residential IP Controller)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/proxy_lite
ExecStartPre=/bin/bash -c "pkill -f 'openvpn.*tun[0-9]' || true"
ExecStart=/opt/proxy_lite/venv/bin/python -u lite_manager.py
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lite-manager.service
systemctl restart lite-manager.service

# 8. 强制安装 Nginx (用于拦截并修复 /v1/models 强制显示 deepseek 的问题)
echo "[*] 正在安装配置 Nginx 本地网关 (接管 80 端口)..."
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get install -yq nginx certbot python3-certbot-nginx

# 写入默认 HTTP Nginx 代理配置 (包含模型列表拦截)
if grep -q "ssl_certificate" /etc/nginx/sites-available/opencode-proxy 2>/dev/null; then
    echo "[*] 检测到已存在 HTTPS 配置，跳过覆盖..."
else
    cat > /etc/nginx/sites-available/opencode-proxy << NGINXEOF
server {
    listen 80;
    server_name _;

    # 核心拦截逻辑：修复 Docker 内置只能获取到 deepseek 模型的硬编码问题
    location /v1/models {
        default_type application/json;
        return 200 '{"object":"list","data":[{"id":"mimo-v2.5-pro","object":"model","created":1686935002,"owned_by":"opencode"},{"id":"mimo-v2.5-free","object":"model","created":1686935002,"owned_by":"opencode"}]}';
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }
}
NGINXEOF
fi

ln -sf /etc/nginx/sites-available/opencode-proxy /etc/nginx/sites-enabled/opencode-proxy
rm -f /etc/nginx/sites-enabled/default

echo "[*] 测试 Nginx 配置..."
nginx -t || { echo "[!] Nginx 配置存在错误！"; exit 1; }
systemctl reload nginx || systemctl restart nginx

echo ""
echo "[*] ========================================="
echo "[*] 可选步骤: 配置 HTTPS (需要您已有域名并解析到本机 IP)"
echo "[*] ========================================="
echo -n "是否现在配置 HTTPS？(y/N): " > /dev/tty
read SETUP_HTTPS < /dev/tty
SETUP_HTTPS=$(echo "$SETUP_HTTPS" | tr '[:upper:]' '[:lower:]')

if [ "$SETUP_HTTPS" = "y" ] || [ "$SETUP_HTTPS" = "yes" ]; then
    read -p "请输入您的域名 (例如 api.example.com): " USER_DOMAIN < /dev/tty

    if [ -z "$USER_DOMAIN" ]; then
        echo "[!] 域名为空，跳过 HTTPS 配置。"
        FINAL_URL="http://<你的VPS公网IP>/v1/chat/completions"
    else
        # 针对域名更新 Nginx 配置
        sed -i "s/server_name _;/server_name $USER_DOMAIN;/" /etc/nginx/sites-available/opencode-proxy
        systemctl reload nginx

        echo "[*] 正在通过 Certbot 自动申请 Let's Encrypt 证书..."
        certbot --nginx -d "$USER_DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect

        if [ $? -eq 0 ]; then
            echo "[+] ✅ HTTPS 配置成功！"
            FINAL_URL="https://$USER_DOMAIN/v1/chat/completions"
        else
            echo "[!] ⚠️  证书申请失败，请检查域名 DNS 是否已正确解析到本机 IP。"
            echo "[!]     您仍然可以通过 HTTP 访问: http://$USER_DOMAIN/v1/chat/completions"
            FINAL_URL="http://$USER_DOMAIN/v1/chat/completions"
        fi
    fi
else
    echo "[*] 跳过 HTTPS 配置，您可以之后手动运行 certbot 来配置。"
    FINAL_URL="http://<你的VPS公网IP>/v1/chat/completions"
fi

# ================================================================
# 9. 完成汇总
# ================================================================
echo ""
echo "=========================================="
echo "  🎉 OpenCode 代理网关 (极致速度版) - 部署全部完成！"
echo "=========================================="
echo "  API 接口地址: ${FINAL_URL:-http://<你的VPS公网IP>/v1/chat/completions}"
echo "  默认鉴权密钥: sk-mimo (请在客户端使用此密钥)"
echo "=========================================="
echo "💡 客户端配置示例："
echo "   Base URL: $(echo "${FINAL_URL:-http://<你的VPS公网IP>/v1/chat/completions}" | sed 's|/chat/completions||')"
echo "   API Key:  sk-mimo"
echo "   Model:    mimo-v2.5-pro"
echo "=========================================="
echo "💡 常用维护命令："
echo "   查看 Lite Manager 日志: journalctl -u lite-manager.service -f"
echo "   重启服务:               systemctl restart lite-manager.service"
echo "   查看流量:               traffic"
echo "   更新证书:               certbot renew"
echo "=========================================="
