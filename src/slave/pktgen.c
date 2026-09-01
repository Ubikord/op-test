/*
 * pktgen.c - генератор/приёмник Ethernet-трафика (с увеличенными буферами)
 * gcc -O2 -Wall -o pktgen pktgen.c
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <signal.h>
#include <getopt.h>

#include <sys/socket.h>
#include <sys/ioctl.h>
#include <netinet/in.h>
#include <net/if.h>
#include <net/ethernet.h>
#include <netpacket/packet.h>

#define MAGIC 0x504B5447u /* "PKTG" */
#define ETH_HDR_LEN 14
#define APP_HDR_LEN 24
#define SENDER_MAC_OFFSET (ETH_HDR_LEN + 16)
#define MIN_FRAME_LEN 60
#define MAX_FRAME_LEN 1496

/* Увеличенные буферы сокетов */
#define RCV_BUF_SIZE (32 * 1024 * 1024)  /* 32 МБ */
#define SND_BUF_SIZE (16 * 1024 * 1024)  /* 16 МБ */

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int signo) {
    (void)signo;
    g_stop = 1;
}

typedef struct {
    char mode[8];
    char iface[IFNAMSIZ];
    char dst_mac_str[32];
    char src_mac_str[32];
    char size_mode[8];
    int size;
    int size_min;
    int size_max;
    long rate_pps;
    double duration_s;
    uint32_t test_id;
    uint64_t packet_count;   // 0 означает "по времени"
    /* === MULTICAST === */
    int dst_type;   // 0=unicast, 1=multicast, 2=broadcast
    char custom_dst_mac[32]; // если задан явно, переопределяет тип
} Args;

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static int parse_mac(const char *s, uint8_t out[6]) {
    if (!s || strlen(s) == 0) return -1;
    unsigned int b[6];
    if (sscanf(s, "%x:%x:%x:%x:%x:%x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6)
        return -1;
    for (int i = 0; i < 6; i++) out[i] = (uint8_t)b[i];
    return 0;
}

static int get_iface_mac(const char *iface, uint8_t out[6]) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(fd, SIOCGIFHWADDR, &ifr) < 0) { close(fd); return -1; }
    memcpy(out, ifr.ifr_hwaddr.sa_data, 6);
    close(fd);
    return 0;
}

static int get_iface_index(int fd, const char *iface) {
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0) return -1;
    return ifr.ifr_ifindex;
}

static void print_json_error(const char *msg) {
    printf("{\"status\":\"error\",\"message\":\"%s\"}\n", msg);
    fflush(stdout);
}

/* === MULTICAST: функция для задания multicast MAC === */
static void set_multicast_mac(uint8_t *mac) {
    // Стандартный IPv4 multicast MAC: 01:00:5E:00:00:01
    uint8_t default_mcast[] = {0x01, 0x00, 0x5E, 0x00, 0x00, 0x01};
    memcpy(mac, default_mcast, 6);
}

typedef struct {
    uint8_t mac[6];
    uint64_t packets;
    uint64_t bytes;
    uint64_t out_of_order;
    int64_t min_seq;
    int64_t max_seq;
} sender_stat_t;

/* -------------------------- SENDER -------------------------- */

static int run_sender(const Args *a) {
    uint8_t dst_mac[6], src_mac[6];
    // Если задан явный dst_mac, используем его, иначе определяем по типу
    if (strlen(a->dst_mac_str) > 0) {
        if (parse_mac(a->dst_mac_str, dst_mac) != 0) {
            print_json_error("invalid dst_mac");
            return 1;
        }
    } else {
        // Определяем MAC по типу
        if (a->dst_type == 1) {
            set_multicast_mac(dst_mac);
        } else if (a->dst_type == 2) {
            // Broadcast
            uint8_t bcast[] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
            memcpy(dst_mac, bcast, 6);
        } else {
            // Unicast — должен быть задан dst_mac, иначе ошибка
            print_json_error("dst_mac required for unicast");
            return 1;
        }
    }

    if (strlen(a->src_mac_str) > 0) {
        if (parse_mac(a->src_mac_str, src_mac) != 0) {
            print_json_error("invalid src_mac");
            return 1;
        }
    } else if (get_iface_mac(a->iface, src_mac) != 0) {
        print_json_error("cannot read iface mac");
        return 1;
    }

    int sock = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sock < 0) { print_json_error("socket() failed, need root/CAP_NET_RAW"); return 1; }

    /* Увеличиваем буфер отправки */
    int sndbuf = SND_BUF_SIZE;
    if (setsockopt(sock, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf)) < 0) {
        perror("setsockopt SO_SNDBUF");
    }

    int ifindex = get_iface_index(sock, a->iface);
    if (ifindex < 0) { print_json_error("cannot resolve ifindex"); close(sock); return 1; }

    struct sockaddr_ll saddr;
    memset(&saddr, 0, sizeof(saddr));
    saddr.sll_family = AF_PACKET;
    saddr.sll_ifindex = ifindex;
    saddr.sll_halen = ETH_ALEN;
    memcpy(saddr.sll_addr, dst_mac, 6);

    srand((unsigned)time(NULL) ^ (unsigned)getpid());

    uint8_t buf[MAX_FRAME_LEN];
    memset(buf, 0, sizeof(buf));
    memcpy(buf, dst_mac, 6);
    memcpy(buf + 6, src_mac, 6);
    uint16_t ethertype = htons(0x88B5);
    memcpy(buf + 12, &ethertype, 2);

    uint64_t seq = 0;
    uint64_t bytes_sent = 0;
    uint64_t packets_sent = 0;

    uint64_t interval_ns = a->rate_pps > 0 ? (uint64_t)(1000000000.0 / (double)a->rate_pps) : 0;
    uint64_t t_start = now_ns();
    uint64_t t_end = a->duration_s > 0 ? t_start + (uint64_t)(a->duration_s * 1e9) : 0;
    uint64_t next_send = t_start;
    uint64_t target_packets = a->packet_count;

    while (!g_stop) {
        uint64_t t = now_ns();

        if (target_packets > 0) {
            if (packets_sent >= target_packets) break;
        } else {
            if (t_end && t >= t_end) break;
        }

        int frame_len;
        if (strcmp(a->size_mode, "random") == 0) {
            int span = a->size_max - a->size_min + 1;
            frame_len = a->size_min + (span > 0 ? rand() % span : 0);
        } else {
            frame_len = a->size;
        }
        if (frame_len < MIN_FRAME_LEN) frame_len = MIN_FRAME_LEN;
        if (frame_len > MAX_FRAME_LEN) frame_len = MAX_FRAME_LEN;

        uint32_t magic = htonl(MAGIC);
        uint32_t tid = htonl(a->test_id);
        uint64_t seq_be = seq;
        memcpy(buf + ETH_HDR_LEN + 0, &magic, 4);
        memcpy(buf + ETH_HDR_LEN + 4, &tid, 4);
        memcpy(buf + ETH_HDR_LEN + 8, &seq_be, 8);
        memcpy(buf + SENDER_MAC_OFFSET, src_mac, 6);


        ssize_t sent = sendto(sock, buf, (size_t)frame_len, 0,
                               (struct sockaddr *)&saddr, sizeof(saddr));
        if (sent < 0) {
            /* Просто сообщаем об ошибке, но не зацикливаемся */
            if (errno == ENOBUFS || errno == EAGAIN) {
                // небольшая пауза, чтобы не спамить ошибками
                struct timespec req = {0, 1000000}; // 1 мс
                nanosleep(&req, NULL);
                continue;
            }
            print_json_error("sendto failed");
            close(sock);
            return 1;
        }
        bytes_sent += (uint64_t)sent;
        packets_sent++;
        seq++;

        if (interval_ns > 0) {
            next_send += interval_ns;
            uint64_t now = now_ns();
            if (next_send > now) {
                struct timespec req;
                uint64_t diff = next_send - now;
                req.tv_sec = diff / 1000000000ULL;
                req.tv_nsec = diff % 1000000000ULL;
                nanosleep(&req, NULL);
            } else {
                next_send = now;
            }
        }
    }

    double elapsed_s = (double)(now_ns() - t_start) / 1e9;
    close(sock);

    printf("{\"status\":\"ok\",\"role\":\"sender\",\"test_id\":%u,"
           "\"packets_sent\":%llu,\"bytes_sent\":%llu,\"duration_s\":%.3f}\n",
           a->test_id,
           (unsigned long long)packets_sent,
           (unsigned long long)bytes_sent,
           elapsed_s);
    fflush(stdout);
    return 0;
}

/* -------------------------- RECEIVER -------------------------- */

static int run_receiver(const Args *a) {
    int sock = socket(AF_PACKET, SOCK_RAW, htons(0x88B5));
    if (sock < 0) { print_json_error("socket() failed, need root/CAP_NET_RAW"); return 1; }

    /* Увеличиваем буфер приёма до 32 МБ */
    int rcvbuf = RCV_BUF_SIZE;
    if (setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf)) < 0) {
        perror("setsockopt SO_RCVBUF");
    }

    int ifindex = get_iface_index(sock, a->iface);
    if (ifindex < 0) { print_json_error("cannot resolve ifindex"); close(sock); return 1; }

    struct sockaddr_ll saddr;
    memset(&saddr, 0, sizeof(saddr));
    saddr.sll_family = AF_PACKET;
    saddr.sll_protocol = htons(0x88B5);
    saddr.sll_ifindex = ifindex;
    if (bind(sock, (struct sockaddr *)&saddr, sizeof(saddr)) < 0) {
        print_json_error("bind failed");
        close(sock);
        return 1;
    }

    struct timeval tv;
    tv.tv_sec = 1;
    tv.tv_usec = 0;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint64_t packets_received = 0;
    uint64_t bytes_received = 0;
    uint64_t out_of_order = 0;
    int64_t min_seq = -1;
    int64_t max_seq = -1;

    typedef struct {
        uint8_t mac[6];
        uint64_t packets;
        uint64_t bytes;
        uint64_t out_of_order;
        int64_t min_seq;
        int64_t max_seq;
    } sender_stat_t;
    sender_stat_t sender_stats[1024]; // максимум 1024 sender'а (достаточно)
    int sender_count = 0;

    uint64_t t_start = now_ns();
    uint64_t t_end = a->duration_s > 0 ? t_start + (uint64_t)(a->duration_s * 1e9) : 0;

    uint8_t buf[2048];

    sender_stat_t* find_or_add_sender(uint8_t *mac) {
        for (int i = 0; i < sender_count; i++) {
            if (memcmp(sender_stats[i].mac, mac, 6) == 0) {
                return &sender_stats[i];
            }
        }
        if (sender_count < 1024) {
            memcpy(sender_stats[sender_count].mac, mac, 6);
            sender_stats[sender_count].packets = 0;
            sender_stats[sender_count].bytes = 0;
            sender_stats[sender_count].out_of_order = 0;
            sender_stats[sender_count].min_seq = -1;
            sender_stats[sender_count].max_seq = -1;
            return &sender_stats[sender_count++];
        }
        return NULL;
    }

    while (!g_stop) {
        if (t_end && now_ns() >= t_end) break;

        ssize_t n = recv(sock, buf, sizeof(buf), 0);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
            if (errno == EINTR) continue;
            break;
        }
        if (n < ETH_HDR_LEN + APP_HDR_LEN) continue;

        uint32_t magic_be, tid_be;
        uint64_t seq;
        uint8_t sender_mac[6];
        memcpy(&magic_be, buf + ETH_HDR_LEN + 0, 4);
        memcpy(&tid_be, buf + ETH_HDR_LEN + 4, 4);
        memcpy(&seq, buf + ETH_HDR_LEN + 8, 8);
        memcpy(sender_mac, buf + SENDER_MAC_OFFSET, 6);

        if (ntohl(magic_be) != MAGIC) continue;
        if (ntohl(tid_be) != a->test_id) continue;
        
        sender_stat_t *stat = find_or_add_sender(sender_mac);
        if (!stat) continue; // превышено количество sender'ов
        stat->packets++;
        stat->bytes += n;

        int64_t s = (int64_t)seq;
        if (stat->min_seq < 0 || s < stat->min_seq) stat->min_seq = s;
        if (stat->max_seq < 0 || s > stat->max_seq) {
            stat->max_seq = s;
        } else {
           stat->out_of_order++;
        }
    }

    close(sock);
    double elapsed_s = (double)(now_ns() - t_start) / 1e9;

    uint64_t expected = (max_seq >= 0 && min_seq >= 0) ? (uint64_t)(max_seq - min_seq + 1) : 0;
    int64_t lost = (int64_t)expected - (int64_t)packets_received + (int64_t)out_of_order;
    if (lost < 0) lost = 0;

    +   printf("{\"status\":\"ok\",\"role\":\"receiver\",\"test_id\":%u,\"duration_s\":%.3f,\"sender_stats\":[",
           a->test_id, elapsed_s);
    int first = 1;
    for (int i = 0; i < sender_count; i++) {
        sender_stat_t *stat = &sender_stats[i];
        if (!first) printf(",");
        first = 0;
        uint64_t expected = (stat->max_seq >= 0 && stat->min_seq >= 0) ? (uint64_t)(stat->max_seq - stat->min_seq + 1) : 0;
        int64_t lost = (int64_t)expected - (int64_t)stat->packets + (int64_t)stat->out_of_order;
        if (lost < 0) lost = 0;
        printf("{\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\","
               "\"packets_received\":%llu,\"bytes_received\":%llu,"
               "\"packets_expected\":%llu,\"packets_lost\":%lld,\"out_of_order\":%llu}",
               stat->mac[0], stat->mac[1], stat->mac[2], stat->mac[3], stat->mac[4], stat->mac[5],
               (unsigned long long)stat->packets,
               (unsigned long long)stat->bytes,
               (unsigned long long)expected,
               (long long)lost,
               (unsigned long long)stat->out_of_order);
    }
    printf("]}\n");
    fflush(stdout);
    return 0;
}

/* -------------------------- MAIN / ARGS -------------------------- */

static void usage(const char *prog) {
    fprintf(stderr,
        "Usage: %s --mode send|recv --iface IFACE --test-id N [options]\n"
        "  --dst-mac MAC        (обязателен для unicast, может быть multicast/broadcast при явном указании)\n"
        "  --src-mac MAC        (опционально, иначе берётся MAC интерфейса)\n"
        "  --size-mode fixed|random  (default fixed)\n"
        "  --size N             (для fixed, default 512)\n"
        "  --size-min N --size-max N  (для random, default 64/1500)\n"
        "  --rate-pps N         (default 1000, для send)\n"
        "  --duration SEC       (default 5.0; для recv 0 = бесконечно до SIGTERM)\n"
        "  --count N            (отправить ровно N пакетов, заменяет --duration для sender)\n"
        "  --multicast          использовать multicast MAC (01:00:5E:00:00:01)\n"
        "  --broadcast          использовать broadcast MAC (FF:FF:FF:FF:FF:FF)\n",
        prog);
}

int main(int argc, char **argv) {
    Args a;
    memset(&a, 0, sizeof(a));
    strcpy(a.size_mode, "fixed");
    a.size = 512;
    a.size_min = 64;
    a.size_max = 1500;
    a.rate_pps = 1000;
    a.duration_s = 5.0;
    a.packet_count = 0;
    a.dst_type = 0;  // по умолчанию unicast

    static struct option long_opts[] = {
        {"mode", required_argument, 0, 0},
        {"iface", required_argument, 0, 0},
        {"dst-mac", required_argument, 0, 0},
        {"src-mac", required_argument, 0, 0},
        {"size-mode", required_argument, 0, 0},
        {"size", required_argument, 0, 0},
        {"size-min", required_argument, 0, 0},
        {"size-max", required_argument, 0, 0},
        {"rate-pps", required_argument, 0, 0},
        {"duration", required_argument, 0, 0},
        {"test-id", required_argument, 0, 0},
        {"count", required_argument, 0, 0},
        /* === MULTICAST === */
        {"multicast", no_argument, 0, 0},
        {"broadcast", no_argument, 0, 0},
        {0, 0, 0, 0}
    };

    int opt_index;
    int c;
    while ((c = getopt_long(argc, argv, "", long_opts, &opt_index)) != -1) {
        if (c != 0) continue;
        const char *name = long_opts[opt_index].name;
        if (!strcmp(name, "mode")) strncpy(a.mode, optarg, sizeof(a.mode) - 1);
        else if (!strcmp(name, "iface")) strncpy(a.iface, optarg, sizeof(a.iface) - 1);
        else if (!strcmp(name, "dst-mac")) strncpy(a.dst_mac_str, optarg, sizeof(a.dst_mac_str) - 1);
        else if (!strcmp(name, "src-mac")) strncpy(a.src_mac_str, optarg, sizeof(a.src_mac_str) - 1);
        else if (!strcmp(name, "size-mode")) strncpy(a.size_mode, optarg, sizeof(a.size_mode) - 1);
        else if (!strcmp(name, "size")) a.size = atoi(optarg);
        else if (!strcmp(name, "size-min")) a.size_min = atoi(optarg);
        else if (!strcmp(name, "size-max")) a.size_max = atoi(optarg);
        else if (!strcmp(name, "rate-pps")) a.rate_pps = atol(optarg);
        else if (!strcmp(name, "duration")) a.duration_s = atof(optarg);
        else if (!strcmp(name, "test-id")) a.test_id = (uint32_t)strtoul(optarg, NULL, 10);
        else if (!strcmp(name, "count")) a.packet_count = strtoull(optarg, NULL, 10);
        /* === MULTICAST === */
        else if (!strcmp(name, "multicast")) a.dst_type = 1;
        else if (!strcmp(name, "broadcast")) a.dst_type = 2;
    }

    if (strlen(a.mode) == 0 || strlen(a.iface) == 0) {
        usage(argv[0]);
        return 2;
    }

    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    if (!strcmp(a.mode, "send")) {
        // Если задан dst_mac, он приоритетнее multicast/broadcast
        if (strlen(a.dst_mac_str) > 0) {
            // уже задан
        } else if (a.dst_type == 0) {
            // unicast без dst_mac — ошибка
            print_json_error("dst-mac required for unicast");
            return 2;
        }
        if (a.packet_count > 0) {
            a.duration_s = 0.0;
        }
        return run_sender(&a);
    } else if (!strcmp(a.mode, "recv")) {
        return run_receiver(&a);
    } else {
        usage(argv[0]);
        return 2;
    }
}