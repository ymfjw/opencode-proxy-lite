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

# 2. 准备目录并拷贝文件
echo "[*] 正在创建工作目录并拉取项目文件..."
mkdir -p /opt/proxy_lite
cd /opt/proxy_lite

# 支持从 GitHub 自动拉取代码
if [ ! -f "lite_manager.py" ]; then
    echo "[*] 未检测到本地源码，正在尝试从 GitHub 拉取代码..."
    
    # 这里的占位符将在上传 GitHub 时由您自行确定
    REPO_URL="https://github.com/ymfjw/opencode-proxy-lite.git"
    
    if [[ "$REPO_URL" == *"YOUR_GITHUB_USERNAME"* ]]; then
        echo "[-] 错误：请先在脚本中配置您的 GitHub 仓库地址 (REPO_URL)！"
        exit 1
    fi
    
    # 拉取代码到临时目录并移动出来
    git clone "$REPO_URL" /tmp/opencode_repo
    cp -r /tmp/opencode_repo/* /opt/proxy_lite/
    cp /opt/proxy_lite/traffic.sh /usr/local/bin/traffic 2>/dev/null || true
    chmod +x /opt/proxy_lite/*.py
    chmod +x /usr/local/bin/traffic 2>/dev/null || true
    rm -rf /tmp/opencode_repo
else
    # 如果已经有文件，直接走本地流程
    cp lite_manager.py proxy_server.py gateway.py /opt/proxy_lite/
    chmod +x /opt/proxy_lite/*.py
fi

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

echo "[*] ========================================"
echo "[+] 安装及部署全部完成！"
echo "[+] 您的 API 网关现已在公网的 8080 端口启动"
echo "[+] 接口地址: http://<你的VPS公网IP>:8080/v1/chat/completions"
echo "[+] 您的专属鉴权密钥 (API Key): $USER_API_KEY"
echo "[*] ========================================"
echo "💡 客户端配置示例："
echo "   Base URL: http://<你的VPS公网IP>:8080/v1"
echo "   API Key:  $USER_API_KEY"
echo "   Model:    mimo-v2.5-pro"
echo "========================================="
echo "💡 常用维护命令："
echo "   - 查看引擎及 AI 网关日志：journalctl -u proxy-lite.service -f"
echo "   - 重启引擎系统：systemctl restart proxy-lite.service"
echo "   - 查看双网卡流量：直接在终端输入 traffic 即可"
echo "========================================="
