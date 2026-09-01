#!/bin/bash
# install_master.sh - Установка на Orange Pi 5
# Использование: ./install_master.sh

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Установка OP-Test Master на Orange Pi 5${NC}"
echo -e "${BLUE}========================================${NC}"

# Проверка, что мы на Orange Pi 5
if [ ! -f "/etc/armbian-release" ] && [ ! -f "/etc/orangepi-release" ]; then
    echo -e "${YELLOW}⚠️ Это может быть не Orange Pi 5, продолжаем...${NC}"
fi

# ============================================================
# ШАГ 1: Установка Python и зависимостей
# ============================================================
echo -e "${BLUE}[1/6] Установка Python и зависимостей...${NC}"

apt update
apt install -y python3 python3-pip python3-pyqt5 python3-pyqt5.qtsvg git sshpass

echo -e "${GREEN}✅ Python и зависимости установлены${NC}"

# ============================================================
# ШАГ 2: Клонирование кода
# ============================================================
echo -e "${BLUE}[2/6] Клонирование кода...${NC}"

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
# ШАГ 3: Установка Python зависимостей
# ============================================================
echo -e "${BLUE}[3/6] Установка Python зависимостей...${NC}"

pip3 install -r requirements.txt

echo -e "${GREEN}✅ Python зависимости установлены${NC}"

# ============================================================
# ШАГ 4: Создание topology.json
# ============================================================
echo -e "${BLUE}[4/6] Создание topology.json...${NC}"

cat > config/topology.json << EOF
{
    "slaves": {
        "r2s_1": {
            "host": "192.168.2.1",
            "port": 5959,
            "interfaces": {
                "eth0": {"mac": "", "vlans": []},
                "eth2": {"mac": "", "vlans": []},
                "eth3": {"mac": "", "vlans": []}
            }
        },
        "r2s_2": {
            "host": "192.168.2.2",
            "port": 5959,
            "interfaces": {
                "eth0": {"mac": "", "vlans": []},
                "eth2": {"mac": "", "vlans": []},
                "eth3": {"mac": "", "vlans": []}
            }
        },
        "r2s_3": {
            "host": "192.168.2.3",
            "port": 5959,
            "interfaces": {
                "eth0": {"mac": "", "vlans": []},
                "eth2": {"mac": "", "vlans": []},
                "eth3": {"mac": "", "vlans": []}
            }
        }
    }
}
EOF

echo -e "${GREEN}✅ topology.json создан${NC}"

# ============================================================
# ШАГ 5: Настройка SSH ключей
# ============================================================
echo -e "${BLUE}[5/6] Настройка SSH ключей...${NC}"

# Генерация ключа если нет
if [ ! -f /root/.ssh/id_rsa ]; then
    ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
    echo -e "${GREEN}✅ SSH ключ создан${NC}"
fi

# Публичный ключ для копирования на R2S
echo ""
echo -e "${YELLOW}Публичный ключ для добавления на R2S:${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"
cat /root/.ssh/id_rsa.pub
echo -e "${BLUE}--------------------------------------------------${NC}"
echo ""

echo -e "${YELLOW}⚠️ Скопируйте этот ключ и добавьте его на каждый R2S:${NC}"
echo -e "   ssh-copy-id root@192.168.2.1"
echo -e "   ssh-copy-id root@192.168.2.2"
echo -e "   ssh-copy-id root@192.168.2.3"
echo ""

# ============================================================
# ШАГ 6: Создание desktop файла
# ============================================================
echo -e "${BLUE}[6/6] Создание desktop файла...${NC}"

cat > /usr/share/applications/op-test.desktop << EOF
[Desktop Entry]
Name=OP-Test
Comment=Тестирование коммутаторов
Exec=python3 /root/op-test/run_gui.py /root/op-test/config/topology.json
Icon=/root/op-test/icon.png
Terminal=false
Type=Application
Categories=Network;
EOF

echo -e "${GREEN}✅ Desktop файл создан${NC}"

# ============================================================
# ЗАВЕРШЕНИЕ
# ============================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ Установка завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Для запуска:${NC}"
echo -e "   cd /root/op-test"
echo -e "   python3 run_gui.py config/topology.json"
echo ""
echo -e "${BLUE}Или через меню приложений: OP-Test${NC}"
echo ""
echo -e "${YELLOW}⚠️ Добавьте SSH ключ на все R2S перед запуском${NC}"