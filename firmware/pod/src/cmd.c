#include "cmd.h"
#include "app.h"
#include "uart.h"
#include "tl.h"
#include "diag.h"
#include "fw_info.h"
#include "i2c.h"
#include "lis3dh.h"
#include "hcsr04.h"
#include "stm32f0xx.h"

#define LINE_MAX 80
#define ARGV_MAX 6

static char line[LINE_MAX];
static uint32_t line_len;
static uint8_t line_overflow;

static int streq(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int parse_num(const char *s, uint32_t *out)   /* decimal or 0x hex */
{
    uint32_t v = 0, base = 10;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
    if (!*s) return 0;
    for (; *s; s++) {
        uint32_t d;
        if (*s >= '0' && *s <= '9') d = (uint32_t)(*s - '0');
        else if (base == 16 && *s >= 'a' && *s <= 'f') d = (uint32_t)(*s - 'a' + 10);
        else if (base == 16 && *s >= 'A' && *s <= 'F') d = (uint32_t)(*s - 'A' + 10);
        else return 0;
        v = v * base + d;
    }
    *out = v;
    return 1;
}

static void end_ok(void)
{
    tl_resp_begin();
    tl_s("end", "1");
    tl_u("ok", 1);
    tl_end();
}

static void end_err(const char *msg)
{
    tl_resp_begin();
    tl_s("end", "1");
    tl_u("ok", 0);
    tl_q("err", msg);
    tl_end();
}

/* ---- handlers ------------------------------------------------------------ */

static void c_help(void)
{
    tl_resp_begin();
    tl_q("cmds", "help info health stats crash[clear] safe(on|off) reset");
    tl_end();
    tl_resp_begin();
    tl_q("sensors", "acc(regs|init|on|off) us(check|on|off) stream(a|d|f)(on|off) rate(acc|us)<hz>");
    tl_end();
    tl_resp_begin();
    tl_q("i2c", "i2c scan | i2c r <addr> <reg> [n] | i2c w <addr> <reg> <val> | i2c lines | i2c recover");
    tl_end();
#if FAULT_INJECT
    tl_resp_begin();
    tl_q("inject", "inject hardfault|hang|acc_glitch|acc_gone|clear");
    tl_end();
#endif
    end_ok();
}

static void c_info(void)
{
    char rst[32];
    diag_reset_str(g_diag.reset_flags, rst, sizeof rst);
    const uint32_t *uid = (const uint32_t *)UID_BASE;

    tl_resp_begin();
    tl_q("build", g_fw_info.build_id);
    tl_x("crc", g_diag.build_crc, 8);
    tl_x("dev", DBGMCU->IDCODE & 0xFFFu, 3);
    tl_x("rev", DBGMCU->IDCODE >> 16, 4);
    tl_u("flash_kb", *(const uint16_t *)FLASHSIZE_BASE);
    tl_end();
    tl_resp_begin();
    tl_x("uid0", uid[0], 8);
    tl_x("uid1", uid[1], 8);
    tl_x("uid2", uid[2], 8);
    tl_end();
    tl_resp_begin();
    tl_u("sysclk", SystemCoreClock);
    tl_x("rcc_cfgr", RCC->CFGR, 8);
    tl_x("rcc_cfgr2", RCC->CFGR2, 8);
    tl_u("usart_brr", USART2->BRR);
    tl_end();
    tl_resp_begin();
    tl_s("mode", g_diag.mode == MODE_SAFE ? "safe" : "normal");
    tl_u("boots", g_diag.boot_count);
    tl_u("streak", g_diag.boot_streak);
    tl_s("rst", rst);
    tl_u("up", g_uptime_ms);
    tl_x("inject", g_inject_flags, 2);
    tl_end();
    end_ok();
}

static void c_stats(void)
{
    tl_resp_begin();
    tl_u("i2c_ok", g_i2c_stats.ok);
    tl_u("i2c_nack", g_i2c_stats.nack);
    tl_u("i2c_timeout", g_i2c_stats.timeout);
    tl_u("i2c_bus", g_i2c_stats.bus);
    tl_u("i2c_arlo", g_i2c_stats.arlo);
    tl_u("i2c_recover", g_i2c_stats.recover);
    tl_s("i2c_last", i2c_err_str(g_i2c_stats.last_err));
    tl_end();
    tl_resp_begin();
    tl_u("tx_bytes", g_uart_stats.tx_bytes);
    tl_u("tx_drop_lines", g_uart_stats.tx_drop_lines);
    tl_u("rx_bytes", g_uart_stats.rx_bytes);
    tl_u("rx_overrun", g_uart_stats.rx_overrun);
    tl_u("rx_drop", g_uart_stats.rx_drop);
    tl_end();
    tl_resp_begin();
    tl_s("acc", app_acc_state_str());
    tl_u("acc_errors", g_app.acc_errors);
    tl_u("acc_cfg_lost", g_app.acc_cfg_lost);
    tl_u("acc_reinit", g_app.acc_reinit);
    tl_u("acc_stale_run", g_app.acc_stale_run);
    tl_u("acc_period_ms", g_app.acc_period_ms);
    tl_u("us_period_ms", g_app.us_period_ms);
    tl_u("stk_free", diag_stack_free());
    tl_end();
    end_ok();
}

static void c_crash(int argc, char **argv)
{
    if (argc > 1 && streq(argv[1], "clear")) {
        diag_clear_crash();
        end_ok();
        return;
    }
    diag_crash_t c;
    tl_resp_begin();
    if (!diag_get_crash(&c)) {
        tl_u("has", 0);
        tl_end();
        end_ok();
        return;
    }
    tl_u("has", 1);
    tl_x("build_crc", c.build_crc, 8);
    tl_u("same_build", c.build_crc == g_diag.build_crc);
    tl_x("pc", c.pc, 8);
    tl_x("lr", c.lr, 8);
    tl_x("sp", c.sp, 8);
    tl_x("xpsr", c.xpsr, 8);
    tl_end();
    tl_resp_begin();
    tl_x("r0", c.r0, 8);
    tl_x("r1", c.r1, 8);
    tl_x("r2", c.r2, 8);
    tl_x("r3", c.r3, 8);
    tl_x("r12", c.r12, 8);
    tl_u("up", c.uptime_ms);
    tl_s("cp", diag_cp_name(c.checkpoint));
    tl_u("count", g_diag.crash_count);
    tl_end();
    end_ok();
}

static void c_i2c(int argc, char **argv)
{
    if (!g_app.sensors_on) i2c_init();   /* allow poking the bus in safe mode */
    uint32_t a, r, v, n = 1;
    if (argc >= 2 && streq(argv[1], "scan")) {
        tl_resp_begin();
        tl_s("found", "");
        for (uint32_t addr = 0x08; addr < 0x78; addr++) {
            if (i2c_probe((uint8_t)addr) == I2C_OK) { uart_puts("0x"); uart_put_hex(addr, 2); uart_putc(','); }
        }
        tl_end();
        end_ok();
    } else if (argc >= 4 && streq(argv[1], "r") && parse_num(argv[2], &a) && parse_num(argv[3], &r)) {
        if (argc >= 5 && (!parse_num(argv[4], &n) || n < 1 || n > 16)) { end_err("n must be 1-16"); return; }
        uint8_t buf[16];
        int rc = i2c_read((uint8_t)a, (uint8_t)(n > 1 ? (r | 0x80u) : r), buf, (uint8_t)n);
        tl_resp_begin();
        tl_x("addr", a, 2);
        tl_x("reg", r, 2);
        tl_s("err", i2c_err_str(rc));
        if (!rc) tl_hex_bytes("data", buf, n);
        tl_end();
        if (rc) end_err(i2c_err_str(rc)); else end_ok();
    } else if (argc >= 5 && streq(argv[1], "w") && parse_num(argv[2], &a) && parse_num(argv[3], &r) && parse_num(argv[4], &v)) {
        int rc = i2c_write((uint8_t)a, (uint8_t)r, (uint8_t)v);
        if (rc) end_err(i2c_err_str(rc)); else end_ok();
    } else if (argc >= 2 && streq(argv[1], "lines")) {
        uint32_t l = i2c_lines();
        tl_resp_begin();
        tl_u("scl", l & 1u);
        tl_u("sda", (l >> 1) & 1u);
        tl_end();
        end_ok();
    } else if (argc >= 2 && streq(argv[1], "recover")) {
        int ok = i2c_bus_recover();
        tl_resp_begin();
        tl_u("sda_high", (uint32_t)ok);
        tl_end();
        end_ok();
    } else {
        end_err("usage: i2c scan | r <addr> <reg> [n] | w <addr> <reg> <val> | lines | recover");
    }
}

static void c_acc(int argc, char **argv)
{
    if (argc >= 2 && streq(argv[1], "regs")) {
        static const uint8_t regs[] = { LIS3DH_WHO_AM_I, LIS3DH_CTRL_REG1, LIS3DH_CTRL_REG2, LIS3DH_CTRL_REG3,
                                        LIS3DH_CTRL_REG4, LIS3DH_CTRL_REG5, LIS3DH_CTRL_REG6, LIS3DH_STATUS_REG };
        static const char *const names[] = { "who_am_i", "ctrl1", "ctrl2", "ctrl3", "ctrl4", "ctrl5", "ctrl6", "status" };
        if (!g_app.sensors_on) i2c_init();
        tl_resp_begin();
        tl_x("addr", g_lis3dh_addr, 2);
        int err = 0;
        for (unsigned i = 0; i < sizeof regs; i++) {
            uint8_t v;
            int rc = i2c_read(g_lis3dh_addr, regs[i], &v, 1);
            if (rc) { err = rc; break; }
            tl_x(names[i], v, 2);
        }
        tl_end();
        tl_resp_begin();
        tl_x("expect_ctrl1", ACC_CTRL1_VALUE, 2);
        tl_x("expect_ctrl4", ACC_CTRL4_VALUE, 2);
        tl_u("mg_per_digit", ACC_MG_PER_DIGIT);
        tl_end();
        if (err) end_err(i2c_err_str(err)); else end_ok();
    } else if (argc >= 2 && streq(argv[1], "init")) {
        if (!g_app.sensors_on) { end_err("sensors are off (safe mode?)"); return; }
        int rc = app_acc_reinit("command");
        if (rc) end_err("init failed"); else end_ok();
    } else if (argc >= 2 && streq(argv[1], "off")) {
        g_app.acc_state = ACC_OFF;
        g_app.stream_a = 0;
        end_ok();
    } else if (argc >= 2 && streq(argv[1], "on")) {
        g_app.stream_a = 1;
        if (g_app.sensors_on) app_acc_reinit("command");
        end_ok();
    } else {
        end_err("usage: acc regs | init | on | off");
    }
}

static void c_us(int argc, char **argv)
{
    if (argc >= 2 && streq(argv[1], "check")) {
        uint32_t echo_mv = 0, vdda_mv = 0;
        int sat = 0;
        int rc = hcsr04_echo_check(&echo_mv, &vdda_mv, &sat);
        tl_resp_begin();
        tl_s("result", rc == 0 ? (sat ? "danger_divider_missing" : echo_mv < 2000u ? "warn_low_level" : "ok")
                       : rc == -1 ? "no_echo_pulse" : "echo_stuck_high");
        tl_u("echo_mv", echo_mv);
        tl_u("vdda_mv", vdda_mv);
        tl_u("saturated", (uint32_t)sat);
        tl_end();
        end_ok();
    } else if (argc >= 2 && (streq(argv[1], "on") || streq(argv[1], "off"))) {
        g_app.stream_d = streq(argv[1], "on");
        end_ok();
    } else {
        end_err("usage: us check | on | off");
    }
}

static void c_stream(int argc, char **argv)
{
    if (argc < 3 || !(streq(argv[2], "on") || streq(argv[2], "off"))) { end_err("usage: stream a|d|f on|off"); return; }
    uint8_t on = streq(argv[2], "on");
    if (streq(argv[1], "a")) g_app.stream_a = on;
    else if (streq(argv[1], "d")) g_app.stream_d = on;
    else if (streq(argv[1], "f")) g_app.stream_f = on;
    else { end_err("stream must be a, d or f"); return; }
    end_ok();
}

static void c_rate(int argc, char **argv)
{
    uint32_t hz;
    if (argc < 3 || !parse_num(argv[2], &hz) || hz == 0) { end_err("usage: rate acc|us <hz>"); return; }
    if (streq(argv[1], "acc") && hz <= 500u) g_app.acc_period_ms = 1000u / hz;
    else if (streq(argv[1], "us") && hz <= 16u) g_app.us_period_ms = 1000u / hz;
    else { end_err("acc: 1-500 Hz, us: 1-16 Hz"); return; }
    end_ok();
}

static void c_safe(int argc, char **argv)
{
    if (argc >= 2 && streq(argv[1], "on")) {
        diag_set_safe(1);
        end_ok();
        uart_flush();
        NVIC_SystemReset();
    } else if (argc >= 2 && streq(argv[1], "off")) {
        diag_set_safe(0);
        if (g_diag.mode == MODE_SAFE) {
            g_diag.mode = MODE_NORMAL;
            app_sensors_start();
        }
        end_ok();
    } else {
        end_err("usage: safe on|off");
    }
}

#if FAULT_INJECT
static void c_inject(int argc, char **argv)
{
    if (argc < 2) { end_err("usage: inject hardfault|hang|acc_glitch|acc_gone|clear"); return; }
    if (streq(argv[1], "hardfault")) {
        end_ok();
        uart_flush();
        /* Unaligned word store: HardFault on Cortex-M0. The address goes
         * through a volatile so the compiler can't see it's misaligned and
         * split the store into byte writes. */
        static volatile uintptr_t bad_addr = 0x20000001u;
        *(volatile uint32_t *)bad_addr = 0xDEADu;
    } else if (streq(argv[1], "hang")) {
        end_ok();
        uart_flush();
        diag_cp(CP_INJECT_HANG);
        for (;;) {}                                                 /* the watchdog resets us in ~2 s */
    } else if (streq(argv[1], "acc_glitch")) {
        /* What a brownout does to the LIS3DH: every control register back to
         * its power-on value (CTRL_REG1 = 0x07 is power-down mode). */
        static const uint8_t def[6] = { 0x07, 0, 0, 0, 0, 0 };
        int rc = 0;
        for (uint8_t i = 0; i < 6 && !rc; i++) rc = i2c_write(g_lis3dh_addr, (uint8_t)(LIS3DH_CTRL_REG1 + i), def[i]);
        if (rc) end_err(i2c_err_str(rc)); else end_ok();
    } else if (streq(argv[1], "acc_gone")) {
        diag_set_inject(g_inject_flags | INJ_ACC_GONE);
        g_lis3dh_addr = INJ_ACC_GONE_ADDR;
        end_ok();
    } else if (streq(argv[1], "clear")) {
        diag_set_inject(0);
        g_lis3dh_addr = LIS3DH_ADDR;
        end_ok();
    } else {
        end_err("unknown fault");
    }
}
#endif

/* ---- dispatch ------------------------------------------------------------ */

static void exec(char *s)
{
    g_tl_resp_id = 0;
    if (*s == '@') {
        uint32_t id = 0;
        s++;
        while (*s >= '0' && *s <= '9') id = id * 10u + (uint32_t)(*s++ - '0');
        g_tl_resp_id = id;
    }

    char *argv[ARGV_MAX];
    int argc = 0;
    while (*s && argc < ARGV_MAX) {
        while (*s == ' ') *s++ = 0;
        if (!*s) break;
        argv[argc++] = s;
        while (*s && *s != ' ') s++;
    }
    if (*s) *s = 0;
    if (!argc) return;

    const char *c = argv[0];
    if      (streq(c, "help"))   c_help();
    else if (streq(c, "info"))   c_info();
    else if (streq(c, "health")) { app_emit_health(); end_ok(); }
    else if (streq(c, "stats"))  c_stats();
    else if (streq(c, "crash"))  c_crash(argc, argv);
    else if (streq(c, "i2c"))    c_i2c(argc, argv);
    else if (streq(c, "acc"))    c_acc(argc, argv);
    else if (streq(c, "us"))     c_us(argc, argv);
    else if (streq(c, "stream")) c_stream(argc, argv);
    else if (streq(c, "rate"))   c_rate(argc, argv);
    else if (streq(c, "safe"))   c_safe(argc, argv);
#if FAULT_INJECT
    else if (streq(c, "inject")) c_inject(argc, argv);
#endif
    else if (streq(c, "reset"))  { end_ok(); uart_flush(); NVIC_SystemReset(); }
    else end_err("unknown command; try help");
}

void cmd_poll(void)
{
    int ch;
    while ((ch = uart_getc()) >= 0) {
        if (ch == '\r' || ch == '\n') {
            if (line_overflow) {
                g_tl_resp_id = 0;
                end_err("line too long");
            } else if (line_len) {
                line[line_len] = 0;
                exec(line);
            }
            line_len = 0;
            line_overflow = 0;
        } else if (line_len < LINE_MAX - 1u) {
            line[line_len++] = (char)ch;
        } else {
            line_overflow = 1;
        }
    }
}
