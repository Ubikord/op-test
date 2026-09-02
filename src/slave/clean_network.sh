#!/bin/sh
# /root/clean_network.sh
# Запускать перед тестами на каждом R2S

# 1. Отключаем IPv6
sysctl -w net.ipv6.conf.all.disable_ipv6=1 > /dev/null 2>&1
sysctl -w net.ipv6.conf.default.disable_ipv6=1 > /dev/null 2>&1

# 2. Настройка ARP для тестовых интерфейсов
for iface in eth0 eth2 eth3; do
    echo 0 > /proc/sys/net/ipv4/conf/$iface/proxy_arp 2>/dev/null
    echo 2 > /proc/sys/net/ipv4/conf/$iface/arp_announce 2>/dev/null
    echo 1 > /proc/sys/net/ipv6/conf/$iface/disable_ipv6 2>/dev/null
done

# 3. Убираем IP-адреса
ip addr flush dev eth0 2>/dev/null
ip addr flush dev eth2 2>/dev/null
ip addr flush dev eth3 2>/dev/null

# 4. Поднимаем интерфейсы (без IP)
ip link set eth0 up 2>/dev/null
ip link set eth2 up 2>/dev/null
ip link set eth3 up 2>/dev/null

# 5. Отключаем multicast
ip link set dev eth0 multicast off 2>/dev/null
ip link set dev eth2 multicast off 2>/dev/null
ip link set dev eth3 multicast off 2>/dev/null

# 6. Останавливаем службы
/etc/init.d/odhcpd stop 2>/dev/null
/etc/init.d/dnsmasq stop 2>/dev/null
/etc/init.d/firewall stop 2>/dev/null
/etc/init.d/umdns stop 2>/dev/null
/etc/init.d/avahi-daemon stop 2>/dev/null
/etc/init.d/igmpproxy stop 2>/dev/null
/etc/init.d/mcproxy stop 2>/dev/null
/etc/init.d/lldpd stop 2>/dev/null

# 7. Проверка: интерфейсы не в бридже
if brctl show 2>/dev/null | grep -q "eth0\|eth2\|eth3"; then
    echo "  ⚠️ ВНИМАНИЕ: Тестовые интерфейсы в бридже!"
fi

echo "✅ Очистка завершена!"