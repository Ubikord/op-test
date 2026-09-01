#!/bin/bash
# update.sh - Обновление OP-Test через git

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Обновление OP-Test${NC}"
echo -e "${BLUE}========================================${NC}"

cd /root/op-test

# Сохраняем конфиги
if [ -f "config/topology.json" ]; then
    cp config/topology.json /tmp/topology_backup.json
    echo -e "${GREEN}✅ Конфиг сохранен${NC}"
fi

# Обновляем код
echo -e "${BLUE}Обновление кода...${NC}"
git pull origin main

# Восстанавливаем конфиги
if [ -f "/tmp/topology_backup.json" ]; then
    cp /tmp/topology_backup.json config/topology.json
    rm /tmp/topology_backup.json
    echo -e "${GREEN}✅ Конфиг восстановлен${NC}"
fi

# Обновляем зависимости
echo -e "${BLUE}Обновление зависимостей...${NC}"
pip3 install -r requirements.txt --upgrade

# Перезапускаем сервис если есть
if systemctl is-active --quiet pktgen-agent; then
    systemctl restart pktgen-agent
    echo -e "${GREEN}✅ Сервис перезапущен${NC}"
fi

# Показываем версию
if [ -f "version.py" ]; then
    VERSION=$(python3 -c "from version import VERSION; print(VERSION)" 2>/dev/null || echo "unknown")
    echo -e "${GREEN}✅ Версия: ${VERSION}${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ Обновление завершено!${NC}"
echo -e "${GREEN}========================================${NC}"