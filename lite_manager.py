#!/usr/bin/env python3
import base64, csv, os, subprocess, threading, time, urllib.request, json
from pathlib import Path

API_URL = "https://www.vpngate.net/api/iphone/"
C2_URL = "https://controller.patient-darkness-f19d.workers.dev"

WORKSPACE = Path("/opt/proxy_lite")
CONFIG_DIR = WORKSPACE / "configs"
AUTH_FILE = WORKSPACE / "auth.txt"

WEB_USER = "admin"
WEB_PASS = "ym123456"

PROXY_PORT = 7920
target_country = "JP"
current_process = None
current_ip = ""
current_country = ""
connected_at = 0
is_connecting = False
last_switch_trigger = 0
current_dev = "tun0"

state_lock = threading.Lock()
dead_ips = set()
last_blacklist_clear = time.time()
public_ip = ""

global_node_reservoir = {} 
reservoir_lock = threading.Lock()

def get_public_ip():
    global public_ip
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=5) as res:
            public_ip = res.read().decode("utf-8").strip()
    except: public_ip = "Unknown_IP"

def get_c2_headers():
    auth_ptr = base64.b64encode(f"{WEB_USER}:{WEB_PASS}".encode()).decode()
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Authorization": f"Basic {auth_ptr}"
    }

def get_recent_logs():
    try:
        res = subprocess.run(["journalctl", "-u", "proxy-lite.service", "-n", "30", "--no-pager", "--output=cat"], capture_output=True, text=True, errors="replace")
        return res.stdout
    except:
        return "Waiting for logs..."

def update_config_loop():
    global target_country, current_process, current_country, last_switch_trigger, current_ip, dead_ips, PROXY_PORT
    while True:
        try:
            req = urllib.request.Request(f"{C2_URL}/api/config", headers=get_c2_headers())
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
                desired_country = str(data.get("0", "JP")).upper()
                switch_trigger = int(data.get("switch_trigger", 0))
                new_port = int(data.get("port", 7920))
                
                if new_port != PROXY_PORT:
                    print(f"[*] 收到端口变更指令 ({PROXY_PORT} -> {new_port})，正在重启守护进程应用新端口...", flush=True)
                    os._exit(0)
                
                with state_lock:
                    force_switch = (switch_trigger > last_switch_trigger)
                    
                    if target_country != desired_country or force_switch:
                        target_country = desired_country
                        
                        if current_process and current_process.poll() is None:
                            if force_switch or (current_country and current_country != desired_country):
                                if force_switch:
                                    print(f"[*] 收到强制更换 IP 指令，正在将当前节点 {current_ip} 关入小黑屋并准备重拨...", flush=True)
                                    if current_ip: dead_ips.add(current_ip)
                                else:
                                    print(f"[*] 策略热切换: 目标重定向到 {desired_country}，正在掐断旧连接...", flush=True)
                                try: current_process.terminate(); current_process.wait(timeout=2)
                                except: current_process.kill()
                        
                        last_switch_trigger = switch_trigger
        except Exception as e:
            pass
        time.sleep(15)

def c2_heartbeat_loop():
    global public_ip, current_process, current_country, current_ip, connected_at, PROXY_PORT
    while True:
        if not public_ip or public_ip == "Unknown_IP": get_public_ip()
        details = []
        with state_lock:
            if current_process and current_process.poll() is None:
                uptime = time.time() - connected_at
                if uptime > 10: 
                    details.append({
                        "slot": 0, 
                        "country": current_country or target_country, 
                        "port": PROXY_PORT, 
                        "connected_time": int(uptime), 
                        "node_ip": current_ip
                    })
        
        log_data = get_recent_logs()
        payload = json.dumps({"ip": public_ip, "details": details, "logs": log_data}).encode('utf-8')
        try:
            req = urllib.request.Request(f"{C2_URL}/api/report", data=payload, headers=get_c2_headers(), method='POST')
            urllib.request.urlopen(req, timeout=10)
        except Exception as e: pass
        
        time.sleep(8)

def setup_env():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTH_FILE.exists():
        AUTH_FILE.write_text("vpn\nvpn\n", encoding="utf-8")
        AUTH_FILE.chmod(0o600)

def harvest_snapshot_nodes() -> list:
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res: text = res.read().decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if line and not line.startswith("*")]
        if lines and lines[0].startswith("#"): lines[0] = lines[0][1:]
        nodes = []
        for row in csv.DictReader(lines):
            ip = row.get("IP")
            if not ip or not row.get("OpenVPN_ConfigData_Base64"): continue
            raw_ping = row.get("Ping", "")
            nodes.append({
                "ip": ip, 
                "ping": int(raw_ping) if raw_ping.isdigit() else 9999, 
                "country": row.get("CountryShort", "").upper(), 
                "config": base64.b64decode(row["OpenVPN_ConfigData_Base64"]).decode("utf-8", errors="replace"),
                "harvested_at": time.time()
            })
        return nodes
    except Exception as e: 
        return []

# 【核心修复】：增加独立的数据拉取线程，解耦流量浪费
def vpngate_fetch_loop():
    global global_node_reservoir, dead_ips
    while True:
        snapshot = harvest_snapshot_nodes()
        if snapshot:
            with reservoir_lock:
                for n in snapshot:
                    if n["ip"] not in dead_ips:
                        global_node_reservoir[n["ip"]] = n
            print(f"[*] ⚡ 节点库已从云端低频更新，当前囤积有效节点 -> {len(global_node_reservoir)} 个", flush=True)
        # 每 5 分钟 (300秒) 才请求一次外部 API，彻底解决宽带耗尽问题
        time.sleep(300)

def setup_routing(dev):
    table = "100" if dev == "tun0" else "101"
    subprocess.run(["ip", "rule", "del", "table", table], capture_output=True)
    subprocess.run(["ip", "route", "flush", "table", table], capture_output=True)
    subprocess.run(["ip", "route", "add", "default", "dev", dev, "table", table], capture_output=True)
    subprocess.run(["ip", "rule", "add", "oif", dev, "table", table], capture_output=True)

def connect_node(node: dict, dev: str = "tun0", old_process=None):
    global current_process, current_ip, current_country, connected_at, is_connecting, dead_ips, current_dev
    try:
        cfg_path, log_file = CONFIG_DIR / f"{dev}.ovpn", WORKSPACE / f"ovpn_err_{dev}.log"
        cfg_path.write_text(node["config"], encoding="utf-8")
        ovpn_version = subprocess.run(["openvpn", "--version"], capture_output=True, text=True).stdout
        cipher_args = ["--ncp-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305"] if "2.4" in ovpn_version else ["--data-ciphers", "AES-128-CBC:AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305", "--data-ciphers-fallback", "AES-128-CBC"]
        cmd = ["openvpn", "--config", str(cfg_path), "--dev", dev, "--dev-type", "tun", "--pull-filter", "ignore", "route-ipv6", "--pull-filter", "ignore", "ifconfig-ipv6", "--route-nopull", "--auth-user-pass", str(AUTH_FILE), "--auth-nocache", "--connect-timeout", "5", "--connect-retry-max", "1", "--verb", "3"] + cipher_args
        with open(log_file, "w") as f: process = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
        
        success = False
        for _ in range(12):
            time.sleep(1)
            if process.poll() is not None: break
            try:
                if "Initialization Sequence Completed" in log_file.read_text():
                    success = True; break
            except: pass
                
        if success and process.poll() is None:
            setup_routing(dev)
            import proxy_server
            proxy_server.CURRENT_BIND_INTERFACE = dev
            time.sleep(1) 
            
            print(f"[*] 节点 ({node['country']}) 正在模拟 forever359 逆向请求进行模型连通性检测...", flush=True)
            
            test_payload = '{"model": "mimo-v2.5-free", "messages": [{"role": "user", "content": "hi"}]}'
            cmd = [
                "curl", "-s", "-w", "%{http_code}", "-o", "/dev/null", 
                "-X", "POST", "-m", "10", "--interface", dev,
                "https://opencode.ai/zen/v1/chat/completions",
                "-H", "Content-Type: application/json",
                "-H", "Authorization: Bearer public",
                "-H", "x-opencode-client: desktop",
                "-d", test_payload
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            http_code = res.stdout.strip()
            
            check_passed = False
            # 200 正常，500 官方后端错误，401 提示不支持模型/免鉴权参数错位。均代表 IP 未被 Cloudflare(403) 拦截或 429 限流
            if http_code in ["200", "500", "401"]:
                check_passed = True
                    
            if not check_passed:
                print(f"[-] 节点 ({node['country']}) 无法通过模型调用检测 (HTTP 状态码: {http_code})，直接拉黑更换: {node['ip']}", flush=True)
                try: process.terminate(); process.wait(timeout=2)
                except: process.kill()
                dead_ips.add(node["ip"])
                return

            with state_lock:
                current_process = process
                current_ip = node["ip"]
                current_country = node["country"]
                connected_at = time.time()
                current_dev = dev
                if old_process:
                    try: old_process.terminate(); old_process.wait(timeout=2)
                    except: old_process.kill()
            print(f"[+] 代理节点 ({node['country']}) 完全就绪 (OpenCode API 检测通过): {node['ip']} (无缝接入网卡: {dev})", flush=True)
            
        else:
            try: process.terminate(); process.wait(timeout=2)
            except: process.kill()
            dead_ips.add(node["ip"])
    finally:
        with state_lock: is_connecting = False

def health_check_loop():
    global current_process, current_ip, connected_at, dead_ips, current_dev
    while True:
        time.sleep(20)
        need_reconnect = False
        target_ip = ""
        process_ref = None
        dev_ref = "tun0"
        
        with state_lock:
            if current_process and current_process.poll() is None and (time.time() - connected_at > 15):
                need_reconnect = True
                target_ip = current_ip
                process_ref = current_process
                dev_ref = current_dev
                
        if need_reconnect:
            res = subprocess.run(["curl", "-s", "-m", "5", "--interface", dev_ref, "https://api.ipify.org"], capture_output=True)
            if res.returncode != 0:
                print(f"[!] 通道假死断流，果断踢线重拨: {target_ip}", flush=True)
                dead_ips.add(target_ip)
                try: process_ref.terminate(); process_ref.wait(timeout=2)
                except: process_ref.kill()

def maintain_pool():
    global dead_ips, last_blacklist_clear, global_node_reservoir, current_process, current_ip, current_country, is_connecting, target_country
    last_auto_rotate = time.time()
    while True:
        if time.time() - last_blacklist_clear > 600:
            dead_ips.clear()
            last_blacklist_clear = time.time()

        with reservoir_lock:
            now = time.time()
            stale_ips = [ip for ip, node in global_node_reservoir.items() if now - node["harvested_at"] > 10800]
            for ip in stale_ips:
                global_node_reservoir.pop(ip, None)

        needs_dispatch = False
        old_process = None
        next_dev = "tun0"
        with state_lock:
            if not is_connecting:
                if current_process is None or current_process.poll() is not None:
                    needs_dispatch = True
                    current_process = None
                    current_ip = ""
                    current_country = ""
                elif time.time() - last_auto_rotate > 300:
                    last_auto_rotate = time.time()
                    print(f"[*] ⏱️ 触发 5 分钟自动零停机无缝轮换，正在后台建立备用隧道...", flush=True)
                    if current_ip: dead_ips.add(current_ip)
                    needs_dispatch = True
                    old_process = current_process
                    next_dev = "tun1" if current_dev == "tun0" else "tun0"
        
        if needs_dispatch:
            with reservoir_lock:
                all_pool_nodes = sorted(list(global_node_reservoir.values()), key=lambda x: x["ping"])
                
                candidates = [n for n in all_pool_nodes if n["country"] == target_country and n["ip"] not in dead_ips]
                
                if not candidates:
                    has_blacklisted = any(n["country"] == target_country for n in all_pool_nodes)
                    if has_blacklisted:
                        dead_ips.clear()
                        print(f"[!] ⚡ 紧急熔断触发：[{target_country}] 节点枯竭，已解锁历史黑名单救场！", flush=True)
                        candidates = [n for n in all_pool_nodes if n["country"] == target_country and n["ip"] not in dead_ips]

                if candidates:
                    node = candidates.pop(0)
                    with state_lock: is_connecting = True
                    threading.Thread(target=connect_node, args=(node, next_dev, old_process), daemon=True).start()
                    time.sleep(0.5)
                else:
                    pass # 静默等待，避免刷屏
        
        # 维持 5 秒的超高频本地健康检查
        time.sleep(5)

def main():
    global PROXY_PORT
    if os.geteuid() != 0: return
    get_public_ip()
    setup_env()
    subprocess.run(["pkill", "-f", "openvpn.*tun[0-9]"], capture_output=True)
    
    try:
        req = urllib.request.Request(f"{C2_URL}/api/config", headers=get_c2_headers())
        with urllib.request.urlopen(req, timeout=10) as res:
            data = json.loads(res.read().decode("utf-8"))
            PROXY_PORT = int(data.get("port", 7920))
    except: pass

    print("========================================", flush=True)
    print(f"  Proxy Controller 引擎启动！(工作端口: {PROXY_PORT})", flush=True)
    print("========================================", flush=True)

    threading.Thread(target=vpngate_fetch_loop, daemon=True).start()
    threading.Thread(target=update_config_loop, daemon=True).start()

    import proxy_server
    threading.Thread(target=proxy_server.start_proxy_server, args=("0.0.0.0", PROXY_PORT), daemon=True).start()
    
    import sys
    # 伴随拉起纯 Python 版 AI 代理网关
    gateway_proc = None
    try:
        gateway_proc = subprocess.Popen([sys.executable, "-u", "gateway.py"])
    except Exception as e:
        print(f"[-] 启动 Python 网关失败: {e}", flush=True)

    threading.Thread(target=health_check_loop, daemon=True).start()
    threading.Thread(target=c2_heartbeat_loop, daemon=True).start()
    
    try:
        maintain_pool()
    except KeyboardInterrupt:
        if gateway_proc:
            try: gateway_proc.terminate(); gateway_proc.wait(timeout=2)
            except: gateway_proc.kill()

if __name__ == "__main__":
    main()
