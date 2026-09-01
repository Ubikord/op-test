#!/bin/bash
# ============================================
# Запуск Master GUI с правильным venv
# ============================================

PROJECT_DIR="/home/orangepi/op-test"
CONFIG_FILE="$PROJECT_DIR/config/topology.json"

# Переходим в проект
cd "$PROJECT_DIR" || exit 1

# Активируем venv (только если не активирован)
if [ -z "$VIRTUAL_ENV" ]; then
    source venv/bin/activate
fi

# Проверка, что мы в правильном venv
echo "✅ Python: $(which python3)"
echo "✅ PyQt5: $(python3 -c 'import PyQt5; print(PyQt5.__file__)' 2>/dev/null || echo 'не найден')"

# Отключаем аппаратное ускорение
export QT_XCB_FORCE_SOFTWARE_OPENGL=1
export LIBGL_ALWAYS_SOFTWARE=1
export QT_QPA_PLATFORM=xcb
export QT_LOGGING_RULES='*=false'

# Запуск
python3 run_gui.py "$CONFIG_FILE"