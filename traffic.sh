#!/bin/bash
echo "====================================="
echo "   OpenCode 代理网关 双网卡流量监控"
echo "====================================="
echo "[1] 物理网卡 (真实公网出口) 流量统计："
vnstat -i ens5 -d | sed -e 's/          day        rx      |     tx      |    total    |   avg. rate/        Date       Download  |   Upload    |    Total    |   Avg. Speed /' | tail -n +4
echo "====================================="
