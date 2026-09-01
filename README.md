# OP-Test — Тестирование коммутаторов
- **Master** (Orange Pi 5): GUI приложение для управления тестами
- **Slave** (Orange Pi R2S): Агенты для генерации и приема трафика
- **Коммутатор**: Тестируемое устройство, соединяющее все интерфейсы
## Установка

### На Orange Pi 5 (Master)
```bash
git clone <репозиторий>
cd op-test
./install/install_master.sh