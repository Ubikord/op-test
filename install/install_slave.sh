#!/bin/sh
# install_slave.sh - Установка OP-Test Slave на Orange Pi R2S
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
if ! echo "$DEVICE_NUMBER" | grep -qE '^[0-9]+$'; then
    echo -e "${RED}❌ Номер устройства должен быть числом${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Устройство: R2S_${DEVICE_NUMBER}${NC}"
echo ""

# ============================================================
# ШАГ 1: Настройка SSH
# ============================================================
echo -e "${BLUE}[1/7] Настройка SSH...${NC}"

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
echo -e "${BLUE}[2/7] Сохранение номера устройства...${NC}"
echo "$DEVICE_NUMBER" > /etc/device_number
echo "$DEVICE_NUMBER" > /root/device_number
echo -e "${GREEN}✅ Номер устройства сохранен: ${DEVICE_NUMBER}${NC}"

# ============================================================
# ШАГ 3: Настройка сети (ПРАВИЛЬНАЯ)
# ============================================================
echo -e "${BLUE}[3/7] Настройка сети...${NC}"

# eth0 - УПРАВЛЯЮЩИЙ интерфейс (192.168.2.x)
uci set network.eth0=interface
uci set network.eth0.proto='static'
uci set network.eth0.ipaddr="192.168.2.${DEVICE_NUMBER}"
uci set network.eth0.netmask='255.255.255.0'
uci set network.eth0.device='eth0'

# eth1 - ТЕСТОВЫЙ интерфейс (10.0.0.x)
uci set network.eth1=interface
uci set network.eth1.proto='static'
uci set network.eth1.ipaddr="10.0.0.${DEVICE_NUMBER}1"
uci set network.eth1.netmask='255.255.255.0'
uci set network.eth1.device='eth1'

# eth2 - ТЕСТОВЫЙ интерфейс
uci set network.eth2=interface
uci set network.eth2.proto='static'
uci set network.eth2.ipaddr="10.0.0.${DEVICE_NUMBER}2"
uci set network.eth2.netmask='255.255.255.0'
uci set network.eth2.device='eth2'

# eth3 - ТЕСТОВЫЙ интерфейс
uci set network.eth3=interface
uci set network.eth3.proto='static'
uci set network.eth3.ipaddr="10.0.0.${DEVICE_NUMBER}3"
uci set network.eth3.netmask='255.255.255.0'
uci set network.eth3.device='eth3'

uci commit network
/etc/init.d/network restart

echo -e "${GREEN}✅ Сеть настроена:${NC}"
echo -e "   eth0: 192.168.2.${DEVICE_NUMBER} (УПРАВЛЕНИЕ)"
echo -e "   eth1: 10.0.0.${DEVICE_NUMBER}1 (ТЕСТОВЫЙ)"
echo -e "   eth2: 10.0.0.${DEVICE_NUMBER}2 (ТЕСТОВЫЙ)"
echo -e "   eth3: 10.0.0.${DEVICE_NUMBER}3 (ТЕСТОВЫЙ)"

# ============================================================
# ШАГ 4: Установка Python и зависимостей (если есть интернет)
# ============================================================
echo -e "${BLUE}[4/7] Установка Python и зависимостей...${NC}"

# Проверка интернета
if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Интернет доступен${NC}"
    
    # Исправление SSL ошибок
    if ! grep -q "check_signature 0" /etc/opkg.conf; then
        echo "option check_signature 0" >> /etc/opkg.conf
        echo "option force_ssl 0" >> /etc/opkg.conf
    fi
    
    # Обновление списков
    opkg update || echo -e "${YELLOW}⚠️ opkg update не удался${NC}"
    
    # Установка пакетов
    opkg install python3 python3-pip gcc git git-http 2>/dev/null || echo -e "${YELLOW}⚠️ Некоторые пакеты не установлены${NC}"
else
    echo -e "${YELLOW}⚠️ Интернет недоступен, пропускаем установку пакетов${NC}"
fi

# ============================================================
# ШАГ 5: Копирование кода
# ============================================================
echo -e "${BLUE}[5/7] Копирование кода...${NC}"

cd /root

# Если код уже есть в op-test-main
if [ -d "op-test/op-test-main" ]; then
    cd op-test/op-test-main
    # Копируем в правильное место
    cd /root
    mkdir -p op-test
    cp -r op-test/op-test-main/* op-test/
    cp -r op-test/op-test-main/isntall op-test/install 2>/dev/null || true
fi

# Если код уже есть в op-test
if [ -d "op-test" ] && [ -f "op-test/run_gui.py" ]; then
    echo -e "${GREEN}✅ Код уже в /root/op-test${NC}"
else
    echo -e "${YELLOW}⚠️ Код не найден, создаем базовую структуру...${NC}"
    mkdir -p /root/op-test/src/slave
    mkdir -p /root/op-test/src/common
    mkdir -p /root/op-test/config
fi

cd /root/op-test

# ============================================================
# ШАГ 6: Создание конфига
# ============================================================
echo -e "${BLUE}[6/7] Создание конфига...${NC}"

cat > config.json << EOF
{
    "interfaces": [
        {"iface": "eth0", "mac": "$(cat /sys/class/net/eth0/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth1", "mac": "$(cat /sys/class/net/eth1/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth2", "mac": "$(cat /sys/class/net/eth2/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth3", "mac": "$(cat /sys/class/net/eth3/address 2>/dev/null || echo 'unknown')"}
    ]
}
EOF

echo -e "${GREEN}✅ Конфиг создан${NC}"

# ============================================================
# ШАГ 7: Компиляция pktgen (если есть исходники)
# ============================================================
echo -e "${BLUE}[7/7] Компиляция pktgen...${NC}"

if [ -f "src/slave/pktgen.c" ]; then
    if command -v gcc >/dev/null 2>&1; then
        gcc -O2 -Wall -o pktgen src/slave/pktgen.c -lm 2>/dev/null
        chmod +x pktgen 2>/dev/null || true
        echo -e "${GREEN}✅ pktgen скомпилирован${NC}"
    else
        echo -e "${YELLOW}⚠️ gcc не найден, pktgen не скомпилирован${NC}"
    fi
else
    echo -e "${YELLOW}⚠️ src/slave/pktgen.c не найден${NC}"
fi

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
echo -e "   Управляющий IP: 192.168.2.${DEVICE_NUMBER}"
echo -e "   Тестовые IP: 10.0.0.${DEVICE_NUMBER}1, 10.0.0.${DEVICE_NUMBER}2, 10.0.0.${DEVICE_NUMBER}3"
echo ""
echo -e "${BLUE}Для подключения с master:${NC}"
echo -e "   ssh root@192.168.2.${DEVICE_NUMBER}"
echo ""
echo -e "${YELLOW}⚠️ Добавьте публичный ключ SSH с master:${NC}"
echo -e "   ssh-copy-id root@192.168.2.${DEVICE_NUMBER}"