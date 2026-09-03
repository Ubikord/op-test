#!/bin/bash
# install_master.sh - Установка OP-Test Master на Orange Pi 5
# Использование: sudo ./install_master.sh
# Устанавливает проект в /home/orangepi/op-test

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

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Запустите скрипт с правами root (sudo)${NC}"
    exit 1
fi

# Определяем пользователя (обычно orangepi)
if [ -n "$SUDO_USER" ]; then
    USER_NAME="$SUDO_USER"
else
    USER_NAME="orangepi"
fi
USER_HOME="/home/$USER_NAME"
PROJECT_DIR="$USER_HOME/op-test"

echo -e "${GREEN}✅ Пользователь: $USER_NAME${NC}"
echo -e "${GREEN}✅ Домашняя папка: $USER_HOME${NC}"
echo -e "${GREEN}✅ Папка проекта: $PROJECT_DIR${NC}"

# ============================================================
# ШАГ 1: Установка Python и зависимостей
# ============================================================
echo -e "${BLUE}[1/7] Установка Python и зависимостей...${NC}"

apt update
apt install -y python3 python3-pip python3-pyqt5 python3-pyqt5.qtsvg git sshpass

echo -e "${GREEN}✅ Python и зависимости установлены${NC}"

# ============================================================
# ШАГ 2: Подготовка кода
# ============================================================
echo -e "${BLUE}[2/7] Подготовка кода...${NC}"

# Если мы запускаем скрипт из папки с кодом (op-test или op-test-main)
CURRENT_DIR="$(pwd)"
if [ -f "$CURRENT_DIR/README.md" ] && [ -d "$CURRENT_DIR/src" ]; then
    echo -e "${GREEN}✅ Код найден в текущей папке: $CURRENT_DIR${NC}"
    
    # Если это op-test-main, переименовываем
    if [ "$(basename "$CURRENT_DIR")" = "op-test-main" ]; then
        echo -e "${YELLOW}⚠️ Переименовываем op-test-main → op-test${NC}"
        cd ..
        mv op-test-main op-test
        PROJECT_DIR="$(pwd)/op-test"
        cd "$PROJECT_DIR"
    else
        PROJECT_DIR="$CURRENT_DIR"
    fi
else
    # Если кода нет, клонируем в домашнюю папку
    echo -e "${YELLOW}⚠️ Код не найден, клонируем репозиторий...${NC}"
    cd "$USER_HOME"
    
    if [ -d "op-test" ]; then
        echo -e "${YELLOW}⚠️ Папка op-test уже существует, обновляем...${NC}"
        cd op-test
        # Меняем URL на SSH чтобы не спрашивал пароль
        git remote set-url origin git@github.com:Ubikord/op-test.git 2>/dev/null || \
        git remote set-url origin https://github.com/Ubikord/op-test.git
        git pull origin main || echo -e "${YELLOW}⚠️ Не удалось обновить${NC}"
    else
        # Пробуем клонировать через SSH
        if git clone git@github.com:Ubikord/op-test.git 2>/dev/null; then
            echo -e "${GREEN}✅ Код склонирован через SSH${NC}"
        else
            echo -e "${YELLOW}⚠️ SSH не работает, используем HTTPS...${NC}"
            git clone https://github.com/Ubikord/op-test.git
            echo -e "${GREEN}✅ Код склонирован через HTTPS${NC}"
        fi
        cd op-test
    fi
    PROJECT_DIR="$USER_HOME/op-test"
fi

# Меняем владельца на пользователя
chown -R "$USER_NAME":"$USER_NAME" "$PROJECT_DIR"
echo -e "${GREEN}✅ Владелец изменен на $USER_NAME${NC}"

cd "$PROJECT_DIR"
echo -e "${GREEN}✅ Рабочая директория: $(pwd)${NC}"

# ============================================================
# ШАГ 3: Установка Python зависимостей
# ============================================================
echo -e "${BLUE}[3/7] Установка Python зависимостей...${NC}"

if [ -f "requirements.txt" ]; then
    pip3 install -r requirements.txt
    echo -e "${GREEN}✅ Python зависимости установлены${NC}"
else
    echo -e "${YELLOW}⚠️ requirements.txt не найден${NC}"
fi

# ============================================================
# ШАГ 4: Создание topology.json
# ============================================================
echo -e "${BLUE}[4/7] Создание topology.json...${NC}"

mkdir -p config
if [ ! -f "config/topology.json" ]; then
    cat > config/topology.json << 'EOF'
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
        }
    }
}
EOF
    echo -e "${GREEN}✅ topology.json создан в config/${NC}"
else
    echo -e "${YELLOW}⚠️ topology.json уже существует${NC}"
fi

chown -R "$USER_NAME":"$USER_NAME" config

# ============================================================
# ШАГ 5: Настройка SSH ключей
# ============================================================
echo -e "${BLUE}[5/7] Настройка SSH ключей...${NC}"

# Генерируем ключ для root
mkdir -p /root/.ssh
chmod 700 /root/.ssh

if [ ! -f /root/.ssh/id_rsa ]; then
    ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
    echo -e "${GREEN}✅ SSH ключ создан для root${NC}"
fi

# Копируем ключ для пользователя
mkdir -p "$USER_HOME/.ssh"
cp /root/.ssh/id_rsa* "$USER_HOME/.ssh/"
chown -R "$USER_NAME":"$USER_NAME" "$USER_HOME/.ssh"
chmod 700 "$USER_HOME/.ssh"
chmod 600 "$USER_HOME/.ssh/id_rsa"
chmod 644 "$USER_HOME/.ssh/id_rsa.pub"

# Добавляем ключ в authorized_keys (для возможности подключения)
cat /root/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys 2>/dev/null || true
cat /root/.ssh/id_rsa.pub >> "$USER_HOME/.ssh/authorized_keys" 2>/dev/null || true
chmod 600 /root/.ssh/authorized_keys 2>/dev/null || true
chown "$USER_NAME":"$USER_NAME" "$USER_HOME/.ssh/authorized_keys" 2>/dev/null || true

echo ""
echo -e "${YELLOW}Публичный ключ для добавления на R2S:${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"
cat /root/.ssh/id_rsa.pub
echo -e "${BLUE}--------------------------------------------------${NC}"
echo ""
echo -e "${YELLOW}⚠️ Скопируйте этот ключ и добавьте на каждый R2S:${NC}"
echo -e "   ssh-copy-id root@192.168.2.1"
echo -e "   ssh-copy-id root@192.168.2.2"
echo -e "   ssh-copy-id root@192.168.2.3"

# ============================================================
# ШАГ 6: Создание run_master.sh
# ============================================================
echo -e "${BLUE}[6/7] Создание run_master.sh...${NC}"

cat > run_master.sh << 'EOF'
#!/bin/bash
# run_master.sh - Запуск OP-Test Master

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Запуск OP-Test из: ${SCRIPT_DIR}"

# Проверяем наличие topology.json
if [ ! -f "${SCRIPT_DIR}/config/topology.json" ]; then
    echo "⚠️ config/topology.json не найден, создаем шаблон..."
    mkdir -p "${SCRIPT_DIR}/config"
    cat > "${SCRIPT_DIR}/config/topology.json" << 'JSON'
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
        }
    }
}
JSON
    echo -e "\033[32m✅ topology.json создан\033[0m"
fi

# Проверяем наличие run_gui.py
if [ ! -f "${SCRIPT_DIR}/run_gui.py" ]; then
    echo "❌ run_gui.py не найден!"
    exit 1
fi

# Запускаем GUI
python3 "${SCRIPT_DIR}/run_gui.py" "${SCRIPT_DIR}/config/topology.json"
EOF

chmod +x run_master.sh
chown "$USER_NAME":"$USER_NAME" run_master.sh
echo -e "${GREEN}✅ run_master.sh создан${NC}"

# ============================================================
# ШАГ 7: Создание ярлыков
# ============================================================
echo -e "${BLUE}[7/7] Создание ярлыков...${NC}"

# 1. Ярлык в меню
cat > /usr/share/applications/op-test.desktop << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=OP-Test
Comment=Тестирование коммутаторов
Exec=python3 ${PROJECT_DIR}/run_gui.py ${PROJECT_DIR}/config/topology.json
Icon=${PROJECT_DIR}/icon.png
Terminal=false
Categories=Network;
StartupNotify=true
EOF

# 2. Ярлык на рабочем столе
DESKTOP_DIR="$USER_HOME/Desktop"
if [ ! -d "$DESKTOP_DIR" ]; then
    DESKTOP_DIR="/root/Desktop"
fi

if [ -d "$DESKTOP_DIR" ]; then
    cat > "$DESKTOP_DIR/op-test.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=OP-Test
Comment=Тестирование коммутаторов
Exec=python3 ${PROJECT_DIR}/run_gui.py ${PROJECT_DIR}/config/topology.json
Icon=${PROJECT_DIR}/icon.png
Terminal=false
Categories=Network;
StartupNotify=true
EOF
    chown "$USER_NAME":"$USER_NAME" "$DESKTOP_DIR/op-test.desktop" 2>/dev/null || true
    chmod +x "$DESKTOP_DIR/op-test.desktop"
    echo -e "${GREEN}✅ Ярлык создан на рабочем столе${NC}"
else
    echo -e "${YELLOW}⚠️ Папка Desktop не найдена${NC}"
fi

# 3. Обновление кэша
update-desktop-database /usr/share/applications/ 2>/dev/null || true
update-menus 2>/dev/null || true

# 4. Создание иконки (если нет)
if [ ! -f "${PROJECT_DIR}/icon.png" ]; then
    if command -v convert >/dev/null 2>&1; then
        convert -size 64x64 xc:blue -fill white -font /usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf -pointsize 24 -gravity center -annotate 0 "OP" "${PROJECT_DIR}/icon.png" 2>/dev/null || true
    fi
    if [ ! -f "${PROJECT_DIR}/icon.png" ]; then
        cp /usr/share/icons/hicolor/64x64/apps/network.png "${PROJECT_DIR}/icon.png" 2>/dev/null || true
    fi
    chown "$USER_NAME":"$USER_NAME" "${PROJECT_DIR}/icon.png" 2>/dev/null || true
fi

echo -e "${GREEN}✅ Ярлыки созданы${NC}"

# ============================================================
# ЗАВЕРШЕНИЕ
# ============================================================
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ Установка завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Информация:${NC}"
echo -e "   Пользователь: $USER_NAME"
echo -e "   Папка проекта: $PROJECT_DIR"
echo -e "   Файл топологии: $PROJECT_DIR/config/topology.json"
echo ""
echo -e "${BLUE}Для запуска:${NC}"
echo -e "   cd $PROJECT_DIR"
echo -e "   ./run_master.sh"
echo -e "   или"
echo -e "   python3 run_gui.py config/topology.json"
echo ""
echo -e "${BLUE}Или через меню приложений: OP-Test${NC}"
echo -e "${BLUE}Или через ярлык на рабочем столе${NC}"
echo ""
echo -e "${YELLOW}⚠️ Добавьте SSH ключ на все R2S перед запуском:${NC}"
echo -e "   ssh-copy-id root@192.168.2.1"
echo -e "   ssh-copy-id root@192.168.2.2"
echo -e "   ssh-copy-id root@192.168.2.3"