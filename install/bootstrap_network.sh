#!/bin/bash
# bootstrap_network.sh - Первичная настройка сети на R2S
# Запуск: ./bootstrap_network.sh <номер_устройства>
# Пример: ./bootstrap_network.sh 4
# Подключается к R2S по 192.168.2.1, настраивает eth0 и lan IP

set -e

DEVICE_NUMBER="$1"
DEFAULT_IP="192.168.2.1"
SLAVE_IP="192.168.2.${DEVICE_NUMBER}"

if [ -z "$DEVICE_NUMBER" ]; then
    echo "❌ Укажите номер устройства"
    echo "Использование: $0 <номер>"
    exit 1
fi

echo "========================================="
echo "  Первичная настройка сети для R2S_${DEVICE_NUMBER}"
echo "========================================="

# ============================================================
# 1. Проверка доступности R2S
# ============================================================
echo "[1/3] Проверка доступности R2S..."

if ! ping -c 1 "$DEFAULT_IP" >/dev/null 2>&1; then
    echo "❌ R2S не найден по IP: $DEFAULT_IP"
    echo "   Проверьте подключение к R2S"
    exit 1
fi

echo "✅ R2S доступен по IP: $DEFAULT_IP"

# Удаляем старый host key
ssh-keygen -f "/home/orangepi/.ssh/known_hosts" -R "$DEFAULT_IP" 2>/dev/null || true

# ============================================================
# 2. Настройка сети на R2S (через SSH)
# ============================================================
echo "[2/3] Настройка сети на R2S..."

ssh -f -o StrictHostKeyChecking=no root@$DEFAULT_IP "sh -c '
    uci set network.eth0=interface
    uci set network.eth0.proto='dhcp'
    uci set network.eth0.device='eth0'
    uci delete network.wan 2>/dev/null
    uci delete network.wan6 2>/dev/null
    uci delete network.@device[0].ports 2>/dev/null
    uci add_list network.@device[0].ports=\"eth1\"
    uci set network.lan.ipaddr=\"192.168.2.${DEVICE_NUMBER}\"
    uci set network.lan.device=br-lan
    uci commit network
    /etc/init.d/network reload
' &"

echo "✅ Сеть настроена на R2S_${DEVICE_NUMBER}"

# ============================================================
# 3. Ожидание переключения на новый IP
# ============================================================
echo "[3/3] Ожидание переключения на новый IP..."

# Пауза для переключения
sleep 5

for i in {1..15}; do
    if ping -c 1 "$SLAVE_IP" >/dev/null 2>&1; then
        echo "✅ R2S доступен по новому IP: $SLAVE_IP"
        break
    fi
    echo "   Ожидание... ($i/15)"
    sleep 2
done

# Удаляем старый host key для нового IP
ssh-keygen -f "/home/orangepi/.ssh/known_hosts" -R "$SLAVE_IP" 2>/dev/null || true

echo ""
echo "========================================="
echo "  ✅ Настройка сети завершена!"
echo "========================================="
echo ""
echo "   Новый IP: $SLAVE_IP"
echo ""
echo "   Теперь запустите: ./install/add_slave.sh $DEVICE_NUMBER"