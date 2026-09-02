#!/bin/sh
# install_slave.sh - Установка OP-Test Slave на Orange Pi R2S
# Использование: ./install_slave.sh <номер_устройства>
# Пример: ./install_slave.sh 4

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
echo -e "${BLUE}  Установка OP-Test Slave для устройства ${DEVICE_NUMBER}${NC}"
echo -e "${BLUE}========================================${NC}"

# ============================================================
# 1. Проверка: мы в папке с распакованным архивом
# ============================================================
if [ ! -f "README.md" ] || [ ! -d "src" ]; then
    echo -e "${RED}❌ Запустите скрипт из папки с распакованным архивом (op-test-main)${NC}"
    exit 1
fi

# ============================================================
# 2. Настройка SSH (ВКЛЮЧАЕМ ПАРОЛЬ ВРЕМЕННО)
# ============================================================
echo -e "${BLUE}[1/7] Настройка SSH (временный пароль)...${NC}"
uci set dropbear.@dropbear[0].RootLogin='1'
uci set dropbear.@dropbear[0].PasswordAuth='1'
uci set dropbear.@dropbear[0].RootPasswordAuth='1'
uci commit dropbear
/etc/init.d/dropbear restart
echo -e "${GREEN}✅ SSH настроен (пароль включен временно)${NC}"

# ============================================================
# 3. Добавление публичного ключа (если передан)
# ============================================================
if [ -f "/tmp/authorized_keys" ]; then
    echo -e "${BLUE}[2/7] Добавление публичного ключа...${NC}"
    mkdir -p /root/.ssh
    cat /tmp/authorized_keys >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    echo -e "${GREEN}✅ Публичный ключ добавлен${NC}"
else
    echo -e "${YELLOW}⚠️ Публичный ключ не передан (файл /tmp/authorized_keys отсутствует)${NC}"
    echo -e "${YELLOW}   Добавьте ключ вручную: ssh-copy-id root@192.168.2.${DEVICE_NUMBER}${NC}"
fi

# ============================================================
# 4. Настройка имени устройства (просто номер)
# ============================================================
echo -e "${BLUE}[3/7] Настройка имени устройства...${NC}"
echo "$DEVICE_NUMBER" > /proc/sys/kernel/hostname
uci set system.@system[0].hostname="$DEVICE_NUMBER"
uci commit system
echo -e "${GREEN}✅ Имя устройства: ${DEVICE_NUMBER}${NC}"

# Сохраняем номер устройства
echo "$DEVICE_NUMBER" > /etc/device_number
echo "$DEVICE_NUMBER" > /root/device_number

# ============================================================
# 5. Установка необходимых пакетов
# ============================================================
echo -e "${BLUE}[4/7] Установка пакетов...${NC}"

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
    opkg install python3 gcc ethtool 2>/dev/null || echo -e "${YELLOW}⚠️ Некоторые пакеты не установлены${NC}"
else
    echo -e "${YELLOW}⚠️ Интернет недоступен, пропускаем установку пакетов${NC}"
fi

# ============================================================
# 6. Настройка тестовых интерфейсов (eth0, eth2, eth3)
# ============================================================
echo -e "${BLUE}[5/7] Настройка тестовых интерфейсов...${NC}"

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
echo -e "${GREEN}✅ Тестовые интерфейсы настроены (перезагрузка сети не требуется)${NC}"

# ============================================================
# 7. Копирование файлов агента (в корень /root/op-test)
# ============================================================
echo -e "${BLUE}[6/7] Копирование файлов агента...${NC}"

# Создаем рабочую папку
mkdir -p /root/op-test

# Копируем ТОЛЬКО файлы агента (без src папок)
if [ -f "src/slave/agent.py" ]; then
    cp src/slave/agent.py /root/op-test/
    echo -e "${GREEN}✅ agent.py скопирован${NC}"
fi

if [ -f "src/common/protocol.py" ]; then
    cp src/common/protocol.py /root/op-test/
    echo -e "${GREEN}✅ protocol.py скопирован${NC}"
fi

if [ -f "src/slave/pktgen.c" ]; then
    cp src/slave/pktgen.c /root/op-test/
    echo -e "${GREEN}✅ pktgen.c скопирован${NC}"
fi

# Копируем clean_network.sh если есть
if [ -f "src/slave/clean_network.sh" ]; then
    cp src/slave/clean_network.sh /root/op-test/
    chmod +x /root/op-test/clean_network.sh
    echo -e "${GREEN}✅ clean_network.sh скопирован${NC}"
fi

# ============================================================
# 8. Компиляция pktgen и создание конфига
# ============================================================
echo -e "${BLUE}[7/7] Компиляция pktgen и создание конфига...${NC}"

cd /root/op-test

# Компиляция pktgen
if [ -f "pktgen.c" ]; then
    if command -v gcc >/dev/null 2>&1; then
        gcc -O2 -Wall -o pktgen pktgen.c -lm 2>/dev/null
        chmod +x pktgen 2>/dev/null || true
        echo -e "${GREEN}✅ pktgen скомпилирован${NC}"
    else
        echo -e "${YELLOW}⚠️ gcc не найден, pktgen не скомпилирован${NC}"
    fi
fi

# Создание config.json
cat > config.json << EOF
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
# 9. Выключение парольного входа (после добавления ключа)
# ============================================================
echo -e "${BLUE}Выключение парольного входа...${NC}"
uci set dropbear.@dropbear[0].PasswordAuth='0'
uci set dropbear.@dropbear[0].RootPasswordAuth='0'
uci commit dropbear
/etc/init.d/dropbear restart
echo -e "${GREEN}✅ Парольный вход выключен (только ключи)${NC}"

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
echo -e "   Тестовые IP:"
echo -e "     eth0: 10.0.0.${DEVICE_NUMBER}1"
echo -e "     eth2: 10.0.0.${DEVICE_NUMBER}2"
echo -e "     eth3: 10.0.0.${DEVICE_NUMBER}3"
echo -e "   Рабочая папка: /root/op-test"
echo -e "   Файлы: agent.py, protocol.py, pktgen, config.json, clean_network.sh"
echo ""
echo -e "${YELLOW}⚠️ Управляющий IP настройте вручную через Luci:${NC}"
echo -e "   Network → Interfaces → LAN → IPv4 адрес: 192.168.2.${DEVICE_NUMBER}"
echo ""
echo -e "${YELLOW}⚠️ Если ключ не был добавлен автоматически:${NC}"
echo -e "   ssh-copy-id root@192.168.2.${DEVICE_NUMBER}"