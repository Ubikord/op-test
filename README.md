# OP-Test — Тестирование коммутаторов
- **Master** (Orange Pi 5): GUI приложение для управления тестами
- **Slave** (Orange Pi R2S): Агенты для генерации и приема трафика
- **Коммутатор**: Тестируемое устройство, соединяющее все интерфейсы

## Установка
## Установка Master (Orange Pi 5)
```bash
wget --no-check-certificate -O op-test.tar.gz https://github.com/Ubikord/op-test/archive/refs/heads/main.tar.gz && \
tar -xzf op-test.tar.gz && \
mv op-test-main op-test && \
cd op-test && \
chmod +x install/install_master.sh && \
sudo ./install/install_master.sh
```
## Установка Slave (Orange Pi R2S)
1. Первичная настройка сети
Подключитесь к R2S по IP: 192.168.2.1 (логин: root, пароль: orangepi)
Перейдите в Network → Interfaces
Настройте LAN (управляющий интерфейс):
- Device: eth1 (Ethernet Adapter)
- IPv4 address: 192.168.2.X, где X — номер устройства (1, 2, 3...)
- Нажмите Save

Удалите все интерфейсы WAN (если есть)

Создайте новый интерфейс для интернета (eth0):
- Name: eth0
- Protocol: DHCP client
- Device: eth0 (Ethernet Adapter)
- Нажмите Create interface → Save & Apply

Переключитесь на новый IP: 192.168.2.X в браузере
Перезагрузите R2S (чтобы тестовые интерфейсы поднялись)

2. Установка софта на Slave (с Master)
На Master выполните:
```bash
cd /root/op-test
./install/add_slave.sh X
где X — номер устройства (1, 2, 3...)
```
Альтернативный способ (вручную на R2S):
```bash
wget --no-check-certificate -O op-test.tar.gz https://github.com/Ubikord/op-test/archive/refs/heads/main.tar.gz && \
tar -xzf op-test.tar.gz && \
mv op-test-main op-test && \
cd op-test && \
chmod +x install/install_slave.sh && \
./install/install_slave.sh X
```
## Запуск
На Master выполните:
```bash
cd /root/op-test
./run_master.sh
```
Или через ярлык на рабочем столе OP-Test.
Перейдите во вкладку "Управление агентами" и нажмите кнопку "Запустить агентов".
