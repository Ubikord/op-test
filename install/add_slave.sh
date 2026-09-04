#!/bin/bash
# add_slave.sh - Автоматическая установка и добавление R2S
# Запуск: ./add_slave.sh <номер_устройства>
# Пример: ./add_slave.sh 4

set -e

DEVICE_NUMBER="$1"
SLAVE_IP="192.168.2.${DEVICE_NUMBER}"
DEFAULT_IP="192.168.2.1"

if [ -z "$DEVICE_NUMBER" ]; then
    echo "❌ Укажите номер устройства"
    echo "Использование: $0 <номер>"
    exit 1
fi

echo "========================================="
echo "  Добавление R2S_${DEVICE_NUMBER}"
echo "========================================="

# ============================================================
# 1. Проверка доступности R2S
# ============================================================
echo "[1/5] Проверка доступности R2S..."

ssh-keygen -f "/home/orangepi/.ssh/known_hosts" -R "$SLAVE_IP" 2>/dev/null || true

if ping -c 1 "$SLAVE_IP" >/dev/null 2>&1; then
    echo "✅ R2S доступен по IP: $SLAVE_IP"
    TARGET_IP="$SLAVE_IP"
else
    echo "⚠️ R2S не найден по IP: $SLAVE_IP"
    echo "   Пробуем по умолчанию: $DEFAULT_IP"
    if ping -c 1 "$DEFAULT_IP" >/dev/null 2>&1; then
        echo "✅ R2S доступен по IP: $DEFAULT_IP"
        TARGET_IP="$DEFAULT_IP"
    else
        echo "❌ R2S не найден! Проверьте подключение."
        exit 1
    fi
fi

# ============================================================
# 2. Создание папки и копирование файлов агента на R2S
# ============================================================
echo "[2/5] Копирование файлов агента на R2S..."

# Создаем папку на R2S
ssh -o StrictHostKeyChecking=no root@$TARGET_IP "mkdir -p /root/op-test"

# Копируем файлы агента
scp -o StrictHostKeyChecking=no src/slave/agent.py root@$TARGET_IP:/root/op-test/
scp -o StrictHostKeyChecking=no src/common/protocol.py root@$TARGET_IP:/root/op-test/
scp -o StrictHostKeyChecking=no src/slave/pktgen.c root@$TARGET_IP:/root/op-test/
scp -o StrictHostKeyChecking=no src/slave/clean_network.sh root@$TARGET_IP:/root/op-test/ 2>/dev/null || true

# Копируем скрипт установки в /root (не в op-test)
scp -o StrictHostKeyChecking=no install/install_slave.sh root@$TARGET_IP:/root/

echo "✅ Файлы скопированы в /root/op-test"

# ============================================================
# 3. Запуск установки на R2S
# ============================================================
echo "[3/5] Запуск установки на R2S..."

ssh -o StrictHostKeyChecking=no root@$TARGET_IP "chmod +x /root/install_slave.sh && /root/install_slave.sh $DEVICE_NUMBER"

echo "✅ Установка на R2S завершена"
echo "   Новый IP: $SLAVE_IP"

# ============================================================
# 4. Добавление SSH ключа
# ============================================================
echo "[4/5] Добавление SSH ключа..."

# Удаляем старый ключ
ssh-keygen -f "/home/orangepi/.ssh/known_hosts" -R "$SLAVE_IP" 2>/dev/null || true

# Копируем ключ
ssh-copy-id -o StrictHostKeyChecking=no root@$SLAVE_IP

# Проверяем подключение
if ssh -o ConnectTimeout=3 root@$SLAVE_IP "echo OK" 2>/dev/null | grep -q OK; then
    echo "✅ SSH ключ успешно добавлен"
else
    echo "❌ Ошибка добавления SSH ключа"
    exit 1
fi

# ============================================================
# 5. Добавление в topology.json
# ============================================================
echo "[5/5] Добавление в topology.json..."

cd /home/orangepi/op-test

# Получаем MAC-адреса
MAC_ETH0=$(ssh root@$SLAVE_IP "cat /sys/class/net/eth0/address 2>/dev/null" | tr -d '\n')
MAC_ETH2=$(ssh root@$SLAVE_IP "cat /sys/class/net/eth2/address 2>/dev/null" | tr -d '\n')
MAC_ETH3=$(ssh root@$SLAVE_IP "cat /sys/class/net/eth3/address 2>/dev/null" | tr -d '\n')

# Добавляем в topology.json
python3 << EOF
import json

with open('config/topology.json', 'r') as f:
    data = json.load(f)

data['slaves'][f'r2s_{$DEVICE_NUMBER}'] = {
    'host': '$SLAVE_IP',
    'port': 5959,
    'interfaces': {
        'eth0': {'mac': '$MAC_ETH0', 'vlans': []},
        'eth2': {'mac': '$MAC_ETH2', 'vlans': []},
        'eth3': {'mac': '$MAC_ETH3', 'vlans': []}
    }
}

with open('config/topology.json', 'w') as f:
    json.dump(data, f, indent=2)
EOF

echo "✅ R2S_${DEVICE_NUMBER} добавлен в topology.json"
echo ""
echo "========================================="
echo "  ✅ Готово!"
echo "========================================="
echo ""
echo "   Устройство: R2S_${DEVICE_NUMBER}"
echo "   IP: $SLAVE_IP"
echo "   MAC eth0: $MAC_ETH0"
echo "   MAC eth2: $MAC_ETH2"
echo "   MAC eth3: $MAC_ETH3"
echo "   Рабочая папка на R2S: /root/op-test"
echo ""
echo "   Запустите GUI: ./run_master.sh"