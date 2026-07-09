#!/bin/bash
set -e

# =================================================================
# OpenCode 高并发代理网关 (纯 Python 版) 一键部署脚本
# 仅支持 Ubuntu/Debian 系统 (建议 Ubuntu 22.04+)
# =================================================================

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
  echo "[-] 请使用 root 权限运行此脚本 (sudo bash install_opencode_proxy.sh)"
  exit 1
fi

echo "[*] ========================================"
echo "[*] 开始自动化部署 OpenCode 高级代理网关..."
echo "[*] ========================================"

# 1. 安装系统级依赖
echo "[*] 正在安装底层系统依赖 (openvpn, vnstat, python3, venv)..."
apt-get update
apt-get install -y openvpn vnstat python3 python3-pip python3-venv curl

# 2. 准备目录并从 GitHub 拉取最新代码（始终覆盖，确保版本最新）
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

# 4. 配置 Python 独立虚拟环境 (隔离系统包)
echo "[*] 正在初始化 Python 虚拟环境并安装专属依赖..."
python3 -m venv /opt/proxy_lite/venv
/opt/proxy_lite/venv/bin/pip install --upgrade pip
/opt/proxy_lite/venv/bin/pip install flask curl_cffi

# 5. 交互式配置访问密钥
# 注意: 通过 /dev/tty 读取键盘输入，避免 curl|bash 管道占用 stdin 导致的崩溃
echo "[*] ========================================"
read -p "请输入您要设置的 API 密钥 (留空则自动生成随机密钥): " USER_API_KEY < /dev/tty
if [ -z "$USER_API_KEY" ]; then
    USER_API_KEY=$(cat /proc/sys/kernel/random/uuid | sed 's/-//g')
    echo "[*] 已自动生成安全 API 密钥: $USER_API_KEY"
else
    echo "[*] 已使用您自定义的 API 密钥"
fi

# 6. 配置 Systemd 守护服务
echo "[*] 正在配置开机自启系统服务 proxy-lite.service..."
cat << EOF > /etc/systemd/system/proxy-lite.service
[Unit]
Description=Proxy Core Engine with OpenCode AI Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/proxy_lite
Environment="PROXY_API_KEY=$USER_API_KEY"
# 启动前自动清理残留隧道
ExecStartPre=/bin/bash -c 'pkill -f openvpn.*tun[0-9] || true'
# 使用独立虚拟环境启动主调度器
ExecStart=/opt/proxy_lite/venv/bin/python -u lite_manager.py
Restart=always
RestartSec=5
# 解除进程限制，应对高并发
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable proxy-lite.service
systemctl restart proxy-lite.service

# ================================================================
# 7. 可选：自动配置 Nginx + Certbot 实现 HTTPS 访问
# ================================================================
echo ""
echo "[*] ========================================="
echo "[*] 可选步骤: 配置 HTTPS (需要您已有域名并解析到本机 IP)"
echo "[*] ========================================="
read -p "是否现在配置 HTTPS？(y/N): " SETUP_HTTPS < /dev/tty
SETUP_HTTPS=$(echo "$SETUP_HTTPS" | tr '[:upper:]' '[:lower:]')

if [ "$SETUP_HTTPS" = "y" ] || [ "$SETUP_HTTPS" = "yes" ]; then
    read -p "请输入您的域名 (例如 api.example.com): " USER_DOMAIN < /dev/tty

    if [ -z "$USER_DOMAIN" ]; then
        echo "[!] 域名为空，跳过 HTTPS 配置。"
    else
        echo "[*] 正在安装 Nginx 和 Certbot..."
        apt-get install -y nginx certbot python3-certbot-nginx

        # 写入 Nginx 反向代理配置（先用 HTTP，certbot 会自动升级为 HTTPS）
        cat > /etc/nginx/sites-available/opencode-proxy << NGINXEOF
server {
    listen 80;
    server_name $USER_DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # SSE 流式响应支持
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }
}
NGINXEOF

        # 启用站点
        ln -sf /etc/nginx/sites-available/opencode-proxy /etc/nginx/sites-enabled/opencode-proxy
        rm -f /etc/nginx/sites-enabled/default
        nginx -t && systemctl reload nginx

        echo "[*] 正在通过 Certbot 自动申请 Let's Encrypt 证书..."
        certbot --nginx -d "$USER_DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect

        if [ $? -eq 0 ]; then
            echo "[+] ✅ HTTPS 配置成功！"
            FINAL_URL="https://$USER_DOMAIN/v1/chat/completions"
        else
            echo "[!] ⚠️  证书申请失败，请检查域名 DNS 是否已正确解析到本机 IP。"
            echo "[!]     您仍然可以通过 HTTP 访问: http://$USER_DOMAIN:8080/v1/chat/completions"
            FINAL_URL="http://$USER_DOMAIN:8080/v1/chat/completions"
        fi
    fi
else
    echo "[*] 跳过 HTTPS 配置，您可以之后手动运行 certbot 来配置。"
    FINAL_URL="http://<你的VPS公网IP>:8080/v1/chat/completions"
fi

# ================================================================
# 8. 完成汇总
# ================================================================
echo ""
echo "=========================================="
echo "  🎉 OpenCode 代理网关 - 部署全部完成！"
echo "=========================================="
echo "  API 接口地址: ${FINAL_URL:-http://<你的VPS公网IP>:8080/v1/chat/completions}"
echo "  专属鉴权密钥: $USER_API_KEY"
echo "=========================================="
echo "💡 客户端配置示例："
echo "   Base URL: $(echo "${FINAL_URL:-http://<你的VPS公网IP>:8080/v1/chat/completions}" | sed 's|/chat/completions||')"
echo "   API Key:  $USER_API_KEY"
echo "   Model:    mimo-v2.5-pro"
echo "=========================================="
echo "💡 常用维护命令："
echo "   查看日志:    journalctl -u proxy-lite.service -f"
echo "   重启服务:    systemctl restart proxy-lite.service"
echo "   查看流量:    traffic"
echo "   更新证书:    certbot renew"
echo "=========================================="
