/* Sensor pod firmware for the STM32F042K6 (Nucleo-32).
 *
 * Sensors: HC-SR04 ultrasonic (TRIG A0/PA0, ECHO D3/PB0 via a 10k/18k divider)
 * and LIS3DH accelerometer (I2C1: SCL PB6, SDA PB7, address 0x18).
 *
 * Streams tagged telemetry lines on USART2 at 115200 baud (the probe's
 * virtual COM port), see tl.h for the format:
 *   D  distance, 10 Hz        A  acceleration, 50 Hz      F  radio frame, 1 Hz
 *   H  health, 1 Hz           E  events                   R  command responses
 * Commands are text lines, optionally prefixed "@<id> " (see cmd.c; "help").
 *
 * Self-diagnostics (diag.c): reset cause, crash capture, watchdog with
 * checkpoints, boot-loop detection -> safe mode, stack high-water mark. */
#include <stdint.h>
#include "stm32f0xx.h"
#include "fw_info.h"
#include "app.h"
#include "uart.h"
#include "tl.h"
#include "diag.h"
#include "adc.h"
#include "hcsr04.h"
#include "i2c.h"
#include "lis3dh.h"
#include "frame.h"
#include "cmd.h"

#define LED_PORT      GPIOB
#define LED_PIN       3u
#define UART_BAUD     115200u
#define ACC_RETRY_MS  2000u

__attribute__((section(".fw_info"), used))
const fw_info_t g_fw_info = { FW_INFO_MAGIC, FW_BUILD_ID };

volatile uint32_t g_uptime_ms;
app_t g_app;

static acc_summary_t summary;
static uint8_t frame_buf[FRAME_LEN] __attribute__((aligned(4)));
static uint32_t acc_retry_at;

void SysTick_Handler(void)
{
    g_uptime_ms++;
}

/* 8 MHz HSI -> /2 -> PLL x12 -> 48 MHz. HSI/PREDIV is used rather than the
 * fixed HSI/2 source because CMSIS's SystemCoreClockUpdate() misreads HSI/2 on
 * the F042 and reports 96 MHz (measured on this board; see the hello project). */
static void clock_init(void)
{
    if ((RCC->CFGR & RCC_CFGR_SWS) == RCC_CFGR_SWS_PLL) {
        RCC->CFGR &= ~RCC_CFGR_SW;
        while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_HSI) {}
    }
    RCC->CR &= ~RCC_CR_PLLON;
    while (RCC->CR & RCC_CR_PLLRDY) {}

    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY;
    RCC->CFGR2 = RCC_CFGR2_PREDIV_DIV2;
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_PLLSRC | RCC_CFGR_PLLMUL | RCC_CFGR_HPRE | RCC_CFGR_PPRE))
              | RCC_CFGR_PLLSRC_HSI_PREDIV | RCC_CFGR_PLLMUL12;
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY)) {}
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) {}

    SystemCoreClockUpdate();
}

static void led_init(void)
{
    RCC->AHBENR |= RCC_AHBENR_GPIOBEN;
    LED_PORT->MODER = (LED_PORT->MODER & ~(3u << (LED_PIN * 2))) | (1u << (LED_PIN * 2));
}

/* ---- accelerometer ------------------------------------------------------- */

const char *app_acc_state_str(void)
{
    static const char *const s[] = { "off", "ok", "stale", "err" };
    return s[g_app.acc_state];
}

static void acc_error(int rc)
{
    g_app.acc_errors++;
    if (g_app.acc_state != ACC_ERR || g_app.acc_err != rc) {   /* report changes only */
        tl_begin('E');
        tl_u("t", g_uptime_ms);
        tl_s("ev", "acc_err");
        tl_s("err", rc == -10 ? "bad_who_am_i" : rc == -11 ? "config_mismatch" : i2c_err_str(rc));
        tl_x("addr", g_lis3dh_addr, 2);
        tl_end();
    }
    g_app.acc_state = ACC_ERR;
    g_app.acc_err = rc;
    acc_retry_at = g_uptime_ms + ACC_RETRY_MS;
}

int app_acc_reinit(const char *why)
{
    diag_cp(CP_ACC_INIT);
    int rc = lis3dh_init();
    g_app.acc_reinit++;
    tl_begin('E');
    tl_u("t", g_uptime_ms);
    tl_s("ev", "acc_init");
    tl_s("why", why);
    tl_s("result", rc == 0 ? "ok" : rc == -10 ? "bad_who_am_i" : rc == -11 ? "config_mismatch" : i2c_err_str(rc));
    tl_end();
    if (rc) {
        acc_error(rc);
    } else {
        g_app.acc_state = ACC_OK;
        g_app.acc_err = 0;
        g_app.acc_stale_run = 0;
    }
    return rc;
}

static void acc_task(uint32_t now)
{
    diag_cp(CP_ACC_READ);
    acc_sample_t a;
    int fresh = 0;
    int rc = lis3dh_read(&a, &fresh);
    if (rc) {
        acc_error(rc);
        return;
    }
    g_app.acc_state = fresh ? ACC_OK : ACC_STALE;
    g_app.acc_stale_run = fresh ? 0u : g_app.acc_stale_run + 1u;
    summary_add(&summary, &a, now);
    if (g_app.stream_a) {
        tl_begin_drop('A');
        tl_u("t", now);
        tl_i("x", a.x);
        tl_i("y", a.y);
        tl_i("z", a.z);
        tl_s("st", fresh ? "ok" : "stale");
        tl_end();
    }
}

/* Read the configuration back; if the LIS3DH reset itself (brownout, ESD,
 * loose VCC) it's back in power-down mode and must be reconfigured. */
static void acc_check_task(uint32_t now)
{
    diag_cp(CP_ACC_CHECK);
    uint8_t c1 = 0, c4 = 0;
    int rc = lis3dh_check_config(&c1, &c4);
    if (rc < 0) {
        acc_error(rc);
    } else if (rc == 1) {
        g_app.acc_cfg_lost++;
        tl_begin('E');
        tl_u("t", now);
        tl_s("ev", "acc_cfg_lost");
        tl_x("ctrl1", c1, 2);
        tl_x("ctrl4", c4, 2);
        tl_x("want_ctrl1", ACC_CTRL1_VALUE, 2);
        tl_x("want_ctrl4", ACC_CTRL4_VALUE, 2);
        tl_end();
        app_acc_reinit("config_lost");
    }
}

static void frame_task(uint32_t now)
{
    diag_cp(CP_FRAME);
    uint32_t n = frame_pack(frame_buf, &summary);
    summary_reset(&summary);
    if (n && g_app.stream_f) {
        tl_begin_drop('F');
        tl_u("t", now);
        tl_hex_bytes("hex", frame_buf, n);
        tl_end();
    }
}

/* ---- sensors on/off (safe mode keeps them off) --------------------------- */

void app_sensors_start(void)
{
    g_app.sensors_on = 1;
    hcsr04_init();
    diag_cp(CP_I2C_INIT);
    i2c_init();
    app_acc_reinit("boot");
}

void app_sensors_stop(void)
{
    g_app.sensors_on = 0;
    g_app.acc_state = ACC_OFF;
}

/* ---- health ---------------------------------------------------------------- */

void app_emit_health(void)
{
    char rst[32];
    diag_reset_str(g_diag.reset_flags, rst, sizeof rst);
    g_app.vdda_mv = adc_vdda_mv();
    g_app.temp_c10 = adc_temp_c10(g_app.vdda_mv);

    tl_begin('H');
    tl_u("t", g_uptime_ms);
    tl_s("mode", g_diag.mode == MODE_SAFE ? "safe" : "normal");
    tl_u("boots", g_diag.boot_count);
    tl_u("streak", g_diag.boot_streak);
    tl_u("crashes", g_diag.crash_count);
    tl_u("wdg", g_diag.wdg_count);
    tl_s("rst", rst);
    tl_u("loop_us", g_app.loop_max_us);
    tl_u("stk_free", diag_stack_free());
    tl_u("vdda", g_app.vdda_mv);
    tl_fixed("temp", g_app.temp_c10, 1);
    tl_s("acc", app_acc_state_str());
    tl_u("acc_errs", g_app.acc_errors);
    tl_u("acc_cfg_lost", g_app.acc_cfg_lost);
    tl_u("i2c_err", g_i2c_stats.nack + g_i2c_stats.timeout + g_i2c_stats.bus + g_i2c_stats.arlo);
    tl_s("us", g_app.sensors_on ? hcsr04_status_str(g_app.us_last) : "off");
    tl_u("tx_drop", g_uart_stats.tx_drop_lines);
    tl_u("rx_ovr", g_uart_stats.rx_overrun);
    tl_u("clk", SystemCoreClock);
    tl_u("acc_hz", 1000u / g_app.acc_period_ms);
    tl_end();
    g_app.loop_max_us = 0;
}

/* ---- boot ------------------------------------------------------------------ */

static void print_boot(void)
{
    char rst[32];
    diag_reset_str(g_diag.reset_flags, rst, sizeof rst);

    uart_puts("\n# === sensor pod (STM32F042K6 + HC-SR04 + LIS3DH) ===\n");
    uart_puts("# build ");  uart_puts(g_fw_info.build_id); uart_puts("\n");
    uart_puts("# lines: D distance, A accel, F radio frame, H health, E event, R response. 'help' lists commands\n");

    tl_begin('E');
    tl_u("t", g_uptime_ms);
    tl_s("ev", "boot");
    tl_q("build", g_fw_info.build_id);
    tl_x("crc", g_diag.build_crc, 8);
    tl_s("rst", rst);
    tl_u("boots", g_diag.boot_count);
    tl_u("streak", g_diag.boot_streak);
    tl_u("crashes", g_diag.crash_count);
    tl_u("wdg", g_diag.wdg_count);
    tl_s("last_cp", diag_cp_name(g_diag.last_checkpoint));
    tl_s("mode", g_diag.mode == MODE_SAFE ? "safe" : "normal");
    tl_u("new_build", g_diag.new_build);
    tl_u("clk", SystemCoreClock);
    if (g_inject_flags) tl_x("inject", g_inject_flags, 2);
    tl_end();

    diag_crash_t c;
    if (g_diag.new_crash && diag_get_crash(&c)) {
        tl_begin('E');
        tl_u("t", g_uptime_ms);
        tl_s("ev", "crash");
        tl_x("build_crc", c.build_crc, 8);
        tl_u("same_build", c.build_crc == g_diag.build_crc);
        tl_x("pc", c.pc, 8);
        tl_x("lr", c.lr, 8);
        tl_x("sp", c.sp, 8);
        tl_x("xpsr", c.xpsr, 8);
        tl_x("r0", c.r0, 8);
        tl_x("r1", c.r1, 8);
        tl_x("r2", c.r2, 8);
        tl_x("r3", c.r3, 8);
        tl_x("r12", c.r12, 8);
        tl_u("up", c.uptime_ms);
        tl_s("cp", diag_cp_name(c.checkpoint));
        tl_end();
    }
    if (g_diag.reset_flags & RCC_CSR_IWDGRSTF) {
        tl_begin('E');
        tl_u("t", g_uptime_ms);
        tl_s("ev", "watchdog_reset");
        tl_s("last_cp", diag_cp_name(g_diag.last_checkpoint));
        tl_end();
    }
    if (g_diag.mode == MODE_SAFE) {
        tl_begin('E');
        tl_u("t", g_uptime_ms);
        tl_s("ev", "safe_mode");
        tl_s("reason", g_diag.boot_streak >= DIAG_SAFE_STREAK ? "boot_loop" : "forced");
        tl_u("streak", g_diag.boot_streak);
        tl_end();
        uart_puts("# SAFE MODE: sensors off after repeated crash/watchdog resets. 'safe off' to retry\n");
    }
}

int main(void)
{
    diag_early();
    diag_cp(CP_INIT);
    clock_init();
    SysTick_Config(SystemCoreClock / 1000u);
    diag_wdg_init();
    led_init();
    uart_init(UART_BAUD);
    adc_init();

    g_app.acc_period_ms = 1000u / ACC_STREAM_HZ;
    g_app.us_period_ms  = 1000u / US_STREAM_HZ;
    g_app.stream_a = g_app.stream_d = g_app.stream_f = 1;
    if (g_inject_flags & INJ_ACC_GONE) g_lis3dh_addr = INJ_ACC_GONE_ADDR;

    print_boot();
    if (g_diag.mode == MODE_NORMAL) app_sensors_start();

    uint32_t now = g_uptime_ms;
    uint32_t next_blink = now, next_us = now, next_acc = now, next_sec = now + 1000u;
    for (;;) {
        uint32_t t0 = diag_micros();
        diag_wdg_kick();
        diag_cp(CP_LOOP);
        now = g_uptime_ms;

        if ((int32_t)(now - next_blink) >= 0) {   /* 1 Hz normal, 5 Hz safe mode */
            next_blink = now + (g_diag.mode == MODE_SAFE ? 100u : 500u);
            LED_PORT->ODR ^= 1u << LED_PIN;
        }

        if (g_app.sensors_on) {
            if ((int32_t)(now - next_us) >= 0) {
                next_us = now + g_app.us_period_ms;
                diag_cp(CP_US);
                hcsr04_start(now);
            }
            hcsr04_result_t r;
            if (hcsr04_poll(now, &r)) {
                g_app.us_last = r.status;
                if (g_app.stream_d) {
                    tl_begin_drop('D');
                    tl_u("t", now);
                    tl_u("mm", r.dist_mm);
                    tl_u("us", r.echo_us);
                    tl_s("st", hcsr04_status_str(r.status));
                    tl_end();
                }
            }

            if ((int32_t)(now - next_acc) >= 0) {
                next_acc += g_app.acc_period_ms;
                if ((int32_t)(now - next_acc) > 100) next_acc = now;   /* fell behind: don't burst */
                if (g_app.acc_state != ACC_ERR) acc_task(now);
                else if ((int32_t)(now - acc_retry_at) >= 0) app_acc_reinit("retry");
            }
        }

        if ((int32_t)(now - next_sec) >= 0) {
            next_sec += 1000u;
            if (g_app.sensors_on && g_app.acc_state != ACC_ERR) acc_check_task(now);
            if (g_app.sensors_on) frame_task(now);
            diag_cp(CP_HEALTH);
            app_emit_health();
        }

        diag_cp(CP_CMD);
        cmd_poll();
        diag_tick(now);

        uint32_t dt = diag_micros() - t0;
        if (dt > g_app.loop_max_us) g_app.loop_max_us = dt;
    }
}
