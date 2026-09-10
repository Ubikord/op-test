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

Установка выполняется в **два этапа**:
1. **Первичная настройка сети** — `bootstrap_network.sh`
2. **Установка софта** — `add_slave.sh`

---

### Этап 1: Первичная настройка сети
На **Master** выполните:
```bash
cd op-test
./install/bootstrap_network.sh X
```
,где X — номер устройства (1, 2, 3...)

Переключитесь на новый IP: 192.168.2.X в браузере
Перезагрузите R2S (чтобы тестовые интерфейсы поднялись)

### Этап 2: Установка софта на Slave
На **Master** выполните:
```bash
cd op-test
./install/add_slave.sh X
```
,где X — номер устройства (1, 2, 3...)

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
