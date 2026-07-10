#!/bin/bash
echo "====================================="
echo "   OpenCode 代理网关 双网卡流量监控"
echo "====================================="
echo "[1] 物理网卡 (真实公网出口) 流量统计："
vnstat -i ens5 -d > /tmp/vnstat_d.txt
cat /tmp/vnstat_d.txt | sed -e 's/          day        rx      |     tx      |    total    |   avg. rate/        Date       Download  |   Upload    |    Total    |   Avg. Speed /' | head -n -1 | tail -n +4
MONTH_LINE=$(vnstat -i ens5 -m | grep -E '^[ ]*20[0-9]{2}-[0-9]{2} ' | tail -n 1)
if [ -n "$MONTH_LINE" ]; then
    echo "$MONTH_LINE" | sed 's/^[ ]*20[0-9]\{2\}-[0-9]\{2\}[ ]*/     Sum Total     /'
fi
echo "====================================="
