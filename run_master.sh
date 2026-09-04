#!/bin/bash
# run_master.sh - Запуск OP-Test Master

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Запуск OP-Test из: ${SCRIPT_DIR}"

# Активация виртуального окружения
if [ -f "${SCRIPT_DIR}/venv/bin/activate" ]; then
    echo "Активация venv..."
    source "${SCRIPT_DIR}/venv/bin/activate"
else
    echo "⚠️ venv не найден, создаем..."
    cd "${SCRIPT_DIR}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
fi

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

# Запускаем GUI
python3 "${SCRIPT_DIR}/run_gui.py" "${SCRIPT_DIR}/config/topology.json"