#!/bin/bash
# install_slave.sh - Установка на Orange Pi R2S
# Использование: ./install_slave.sh <номер_устройства>
# Пример: ./install_slave.sh 1

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Установка OP-Test Slave на Orange Pi R2S${NC}"
echo -e "${BLUE}========================================${NC}"

# Проверка аргументов
if [ -z "$1" ]; then
    echo -e "${RED}❌ Ошибка: укажите номер устройства${NC}"
    echo -e "${YELLOW}Использование: $0 <номер_устройства>${NC}"
    echo -e "${YELLOW}Пример: $0 1${NC}"
    exit 1
fi

DEVICE_NUMBER="$1"

# Проверка, что мы на R2S
if [ ! -f "/etc/openwrt_version" ]; then
    echo -e "${RED}❌ Это не Orange Pi R2S с OpenWrt${NC}"
    exit 1
fi

# Проверка номера устройства
if ! [[ "$DEVICE_NUMBER" =~ ^[0-9]+$ ]]; then
    echo -e "${RED}❌ Номер устройства должен быть числом${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Устройство: R2S_${DEVICE_NUMBER}${NC}"
echo ""

# ============================================================
# ШАГ 1: Настройка SSH
# ============================================================
echo -e "${BLUE}[1/9] Настройка SSH...${NC}"

uci set dropbear.@dropbear[0].RootLogin='1'
uci set dropbear.@dropbear[0].PasswordAuth='0'
uci set dropbear.@dropbear[0].RootPasswordAuth='0'
uci commit dropbear
/etc/init.d/dropbear restart

echo -e "${GREEN}✅ SSH настроен (только ключи)${NC}"

# Добавление публичного ключа если есть
if [ -f "/tmp/authorized_keys" ]; then
    mkdir -p /root/.ssh
    cat /tmp/authorized_keys >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    echo -e "${GREEN}✅ SSH ключ добавлен${NC}"
fi

# ============================================================
# ШАГ 2: Сохранение номера устройства
# ============================================================
echo -e "${BLUE}[2/9] Сохранение номера устройства...${NC}"
echo "$DEVICE_NUMBER" > /etc/device_number
echo "$DEVICE_NUMBER" > /root/device_number
echo -e "${GREEN}✅ Номер устройства сохранен: ${DEVICE_NUMBER}${NC}"

# ============================================================
# ШАГ 3: Настройка сети
# ============================================================
echo -e "${BLUE}[3/9] Настройка сети...${NC}"

# Формируем IP адреса
ETH0_IP="10.0.0.${DEVICE_NUMBER}1"
ETH2_IP="10.0.0.${DEVICE_NUMBER}2"
ETH3_IP="10.0.0.${DEVICE_NUMBER}3"
ETH1_IP="192.168.2.${DEVICE_NUMBER}"

# Настройка eth0 - тестовый интерфейс
uci set network.eth0=interface
uci set network.eth0.proto='static'
uci set network.eth0.ipaddr="${ETH0_IP}"
uci set network.eth0.netmask='255.255.255.0'
uci set network.eth0.device='eth0'

# Настройка eth1 - управляющий интерфейс (для master)
uci set network.eth1=interface
uci set network.eth1.proto='static'
uci set network.eth1.ipaddr="${ETH1_IP}"
uci set network.eth1.netmask='255.255.255.0'
uci set network.eth1.device='eth1'

# Настройка eth2 - тестовый интерфейс
uci set network.eth2=interface
uci set network.eth2.proto='static'
uci set network.eth2.ipaddr="${ETH2_IP}"
uci set network.eth2.netmask='255.255.255.0'
uci set network.eth2.device='eth2'

# Настройка eth3 - тестовый интерфейс
uci set network.eth3=interface
uci set network.eth3.proto='static'
uci set network.eth3.ipaddr="${ETH3_IP}"
uci set network.eth3.netmask='255.255.255.0'
uci set network.eth3.device='eth3'

uci commit network
/etc/init.d/network restart

echo -e "${GREEN}✅ Сеть настроена:${NC}"
echo -e "   eth0: ${ETH0_IP} (тестовый)"
echo -e "   eth1: ${ETH1_IP} (управление)"
echo -e "   eth2: ${ETH2_IP} (тестовый)"
echo -e "   eth3: ${ETH3_IP} (тестовый)"

# ============================================================
# ШАГ 4: Установка Python и зависимостей
# ============================================================
echo -e "${BLUE}[4/9] Установка Python и зависимостей...${NC}"

opkg update
opkg install python3 python3-pip gcc git

echo -e "${GREEN}✅ Python и зависимости установлены${NC}"

# ============================================================
# ШАГ 5: Клонирование кода
# ============================================================
echo -e "${BLUE}[5/9] Клонирование кода...${NC}"

cd /root
if [ -d "op-test" ]; then
    echo -e "${YELLOW}⚠️ Директория op-test уже существует, обновление...${NC}"
    cd op-test
    git pull origin main
else
    git clone <ваш_репозиторий> op-test
    cd op-test
fi

echo -e "${GREEN}✅ Код склонирован${NC}"

# ============================================================
# ШАГ 6: Создание конфига
# ============================================================
echo -e "${BLUE}[6/9] Создание конфига...${NC}"

cat > config.json << EOF
{
    "interfaces": [
        {"iface": "eth0", "mac": "$(cat /sys/class/net/eth0/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth2", "mac": "$(cat /sys/class/net/eth2/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth3", "mac": "$(cat /sys/class/net/eth3/address 2>/dev/null || echo 'unknown')"}
    ]
}
EOF

echo -e "${GREEN}✅ Конфиг создан${NC}"

# ============================================================
# ШАГ 7: Компиляция pktgen
# ============================================================
echo -e "${BLUE}[7/9] Компиляция pktgen...${NC}"

gcc -O2 -Wall -o pktgen src/slave/pktgen.c -lm
chmod +x pktgen

echo -e "${GREEN}✅ pktgen скомпилирован${NC}"

# ============================================================
# ШАГ 8: Настройка sysctl для производительности
# ============================================================
echo -e "${BLUE}[8/9] Настройка производительности сети...${NC}"

cat >> /etc/sysctl.conf << EOF

# OP-Test performance settings
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.core.rmem_default = 65536
net.core.wmem_default = 65536
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
EOF

sysctl -p

echo -e "${GREEN}✅ Настройки производительности применены${NC}"

# ============================================================
# ШАГ 9: Создание и запуск сервиса
# ============================================================
echo -e "${BLUE}[9/9] Настройка сервиса...${NC}"

cat > /etc/systemd/system/pktgen-agent.service << EOF
[Unit]
Description=Pktgen Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/op-test
ExecStart=/usr/bin/python3 /root/op-test/src/slave/agent.py --config /root/op-test/config.json --pktgen /root/op-test/pktgen --host 0.0.0.0 --port 5959
Restart=always
RestartSec=10
StandardOutput=append:/var/log/pktgen-agent.log
StandardError=append:/var/log/pktgen-agent.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pktgen-agent
systemctl start pktgen-agent

echo -e "${GREEN}✅ Сервис запущен${NC}"

# ============================================================
# ЗАВЕРШЕНИЕ
# ============================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ Установка завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Информация о устройстве:${NC}"
echo -e "   Номер: ${DEVICE_NUMBER}"
echo -e "   Управляющий IP: ${ETH1_IP}"
echo -e "   Тестовые IP: ${ETH0_IP}, ${ETH2_IP}, ${ETH3_IP}"
echo ""
echo -e "${BLUE}Проверка статуса:${NC}"
echo -e "   systemctl status pktgen-agent"
echo -e "   tail -f /var/log/pktgen-agent.log"
echo ""
echo -e "${YELLOW}⚠️ Добавьте публичный ключ SSH на этот R2S для доступа с master${NC}"