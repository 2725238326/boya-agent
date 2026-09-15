#!/usr/bin/env bash
# Run from the Tencent Cloud VNC/serial console when SSH is unavailable.
set -u

echo '== identity =='
hostnamectl 2>/dev/null || hostname
date -Is

echo '== memory, swap, load =='
free -h
swapon --show --bytes || true
uptime

echo '== top memory processes =='
ps -eo pid,ppid,user,%mem,rss,etime,cmd --sort=-%mem | head -n 16

echo '== listeners =='
ss -lntp 2>/dev/null | grep -E ':(22|80|443|5000)\b' || true

echo '== services =='
for service in ssh sshd nginx boya-agent; do
    if systemctl list-unit-files "${service}.service" --no-legend 2>/dev/null | grep -q .; then
        printf '%s: ' "$service"
        systemctl is-active "$service" || true
    fi
done

echo '== kernel OOM events =='
journalctl -k --since '48 hours ago' --no-pager 2>/dev/null \
    | grep -Ei 'out of memory|oom-killer|killed process|memory cgroup' \
    | tail -n 80 || true

echo '== recent application events =='
journalctl -u boya-agent --since '6 hours ago' --no-pager -n 160 2>/dev/null \
    | grep -Ei '抓取|scrape|系统已就绪|定时调度|browser|memory|killed|error|failed' \
    | tail -n 100 || true
