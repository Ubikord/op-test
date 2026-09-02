#!/bin/sh
# install_from_archive.sh - Установка OP-Test Slave из архива
# Использование: ./install_from_archive.sh <номер_устройства>
# Пример: ./install_from_archive.sh 4

set -e

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ -z "$1" ]; then
    echo -e "${RED}❌ Укажите номер устройства${NC}"
    echo "Использование: $0 <номер>"
    exit 1
fi

DEVICE_NUMBER="$1"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Установка OP-Test Slave для R2S_${DEVICE_NUMBER}${NC}"
echo -e "${BLUE}========================================${NC}"

# ============================================================
# 1. Проверка: мы в папке с распакованным архивом
# ============================================================
if [ ! -f "README.md" ] || [ ! -d "src" ]; then
    echo -e "${RED}❌ Запустите скрипт из папки с распакованным архивом (op-test-main)${NC}"
    exit 1
fi

# ============================================================
# 2. Настройка SSH
# ============================================================
echo -e "${BLUE}[1/7] Настройка SSH...${NC}"
uci set dropbear.@dropbear[0].RootLogin='1'
uci set dropbear.@dropbear[0].PasswordAuth='0'
uci set dropbear.@dropbear[0].RootPasswordAuth='0'
uci commit dropbear
/etc/init.d/dropbear restart
echo -e "${GREEN}✅ SSH настроен (только ключи)${NC}"

# ============================================================
# 3. Настройка имени устройства (hostname)
# ============================================================
echo -e "${BLUE}[2/7] Настройка имени устройства...${NC}"
HOSTNAME="${DEVICE_NUMBER}"
echo "$HOSTNAME" > /proc/sys/kernel/hostname
uci set system.@system[0].hostname="$HOSTNAME"
uci commit system
echo -e "${GREEN}✅ Имя устройства: ${HOSTNAME}${NC}"

# ============================================================
# 4. Настройка br-lan (управляющий интерфейс)
# ============================================================
echo -e "${BLUE}[3/7] Настройка управляющего интерфейса (br-lan)...${NC}"

# br-lan обычно называется 'lan' в UCI
if uci get network.lan >/dev/null 2>&1; then
    # Меняем IP br-lan
    uci set network.lan.ipaddr="192.168.2.${DEVICE_NUMBER}"
    uci commit network
    /etc/init.d/network restart
    echo -e "${GREEN}✅ br-lan IP изменен на 192.168.2.${DEVICE_NUMBER}${NC}"
else
    echo -e "${RED}❌ br-lan не найден!${NC}"
    exit 1
fi

# Сохраняем номер устройства
echo "$DEVICE_NUMBER" > /etc/device_number
echo "$DEVICE_NUMBER" > /root/device_number

# ============================================================
# 5. Настройка тестовых интерфейсов (eth0, eth2, eth3)
# ============================================================
echo -e "${BLUE}[4/7] Настройка тестовых интерфейсов...${NC}"

# eth0 - тестовый интерфейс
if ip link show eth0 >/dev/null 2>&1; then
    uci set network.eth0=interface
    uci set network.eth0.proto='static'
    uci set network.eth0.ipaddr="10.0.0.${DEVICE_NUMBER}1"
    uci set network.eth0.netmask='255.255.255.0'
    uci set network.eth0.device='eth0'
    echo -e "${GREEN}✅ eth0: 10.0.0.${DEVICE_NUMBER}1 (тестовый)${NC}"
fi

# eth2 - тестовый интерфейс
if ip link show eth2 >/dev/null 2>&1; then
    uci set network.eth2=interface
    uci set network.eth2.proto='static'
    uci set network.eth2.ipaddr="10.0.0.${DEVICE_NUMBER}2"
    uci set network.eth2.netmask='255.255.255.0'
    uci set network.eth2.device='eth2'
    echo -e "${GREEN}✅ eth2: 10.0.0.${DEVICE_NUMBER}2 (тестовый)${NC}"
fi

# eth3 - тестовый интерфейс
if ip link show eth3 >/dev/null 2>&1; then
    uci set network.eth3=interface
    uci set network.eth3.proto='static'
    uci set network.eth3.ipaddr="10.0.0.${DEVICE_NUMBER}3"
    uci set network.eth3.netmask='255.255.255.0'
    uci set network.eth3.device='eth3'
    echo -e "${GREEN}✅ eth3: 10.0.0.${DEVICE_NUMBER}3 (тестовый)${NC}"
fi

uci commit network
/etc/init.d/network restart

# ============================================================
# 6. Установка необходимых пакетов
# ============================================================
echo -e "${BLUE}[5/7] Установка пакетов...${NC}"

# Проверяем интернет
if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    echo -e "${GREEN}✅ Интернет доступен${NC}"
    
    # Исправление SSL ошибок
    if ! grep -q "check_signature 0" /etc/opkg.conf; then
        echo "option check_signature 0" >> /etc/opkg.conf
    fi
    
    # Обновление списков
    opkg update || echo -e "${YELLOW}⚠️ opkg update не удался${NC}"
    
    # Установка ТОЛЬКО необходимых пакетов
    echo -e "${YELLOW}Установка python3, gcc...${NC}"
    opkg install python3 gcc 2>/dev/null || echo -e "${YELLOW}⚠️ Некоторые пакеты не установлены${NC}"
else
    echo -e "${YELLOW}⚠️ Интернет недоступен, пропускаем установку пакетов${NC}"
fi

# ============================================================
# 7. Копирование файлов агента
# ============================================================
echo -e "${BLUE}[6/7] Копирование файлов агента...${NC}"

# Создаем рабочую папку
mkdir -p /root/op-test
mkdir -p /root/op-test/src/slave
mkdir -p /root/op-test/src/common

# Копируем ТОЛЬКО файлы агента (без master)
if [ -f "src/slave/agent.py" ]; then
    cp src/slave/agent.py /root/op-test/src/slave/
    echo -e "${GREEN}✅ agent.py скопирован${NC}"
fi

if [ -f "src/common/protocol.py" ]; then
    cp src/common/protocol.py /root/op-test/src/common/
    echo -e "${GREEN}✅ protocol.py скопирован${NC}"
fi

if [ -f "src/slave/pktgen.c" ]; then
    cp src/slave/pktgen.c /root/op-test/
    echo -e "${GREEN}✅ pktgen.c скопирован${NC}"
fi

# Копируем скрипт очистки сети (если есть)
if [ -f "src/slave/clean_network.sh" ]; then
    cp src/slave/clean_network.sh /root/op-test/
    chmod +x /root/op-test/clean_network.sh 2>/dev/null || true
fi

# ============================================================
# 8. Компиляция pktgen
# ============================================================
echo -e "${BLUE}[7/7] Компиляция pktgen...${NC}"

cd /root/op-test

if [ -f "pktgen.c" ]; then
    if command -v gcc >/dev/null 2>&1; then
        gcc -O2 -Wall -o pktgen pktgen.c -lm 2>/dev/null
        chmod +x pktgen 2>/dev/null || true
        echo -e "${GREEN}✅ pktgen скомпилирован${NC}"
    else
        echo -e "${YELLOW}⚠️ gcc не найден, pktgen не скомпилирован${NC}"
    fi
fi

# ============================================================
# 9. Создание конфига
# ============================================================
echo -e "${BLUE}Создание config.json...${NC}"

cat > /root/op-test/config.json << EOF
{
    "interfaces": [
        {"iface": "eth0", "mac": "$(cat /sys/class/net/eth0/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth2", "mac": "$(cat /sys/class/net/eth2/address 2>/dev/null || echo 'unknown')"},
        {"iface": "eth3", "mac": "$(cat /sys/class/net/eth3/address 2>/dev/null || echo 'unknown')"}
    ]
}
EOF

echo -e "${GREEN}✅ config.json создан${NC}"

# ============================================================
# 10. Очистка
# ============================================================
echo -e "${BLUE}Очистка...${NC}"
cd /root
rm -rf op-test-main 2>/dev/null || true
rm -f op-test.tar.gz 2>/dev/null || true

# ============================================================
# ЗАВЕРШЕНИЕ
# ============================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ Установка завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Информация:${NC}"
echo -e "   Устройство: ${DEVICE_NUMBER}"
echo -e "   Управляющий IP: 192.168.2.${DEVICE_NUMBER}"
echo -e "   Тестовые IP:"
echo -e "     eth0: 10.0.0.${DEVICE_NUMBER}1"
echo -e "     eth2: 10.0.0.${DEVICE_NUMBER}2"
echo -e "     eth3: 10.0.0.${DEVICE_NUMBER}3"
echo -e "   Рабочая папка: /root/op-test"
echo ""
echo -e "${YELLOW}⚠️ Подключитесь по новому IP:${NC}"
echo -e "   ssh root@192.168.2.${DEVICE_NUMBER}"
echo ""
echo -e "${YELLOW}⚠️ Добавьте публичный ключ SSH с master:${NC}"
echo -e "   ssh-copy-id root@192.168.2.${DEVICE_NUMBER}"