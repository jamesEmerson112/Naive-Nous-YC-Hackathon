/* HC-SR04 distance streaming firmware for the STM32F042K6 (Nucleo-32).
 *
 * Streams one CSV line per measurement (10 per second) on USART2 at 115200
 * baud, which is the probe's virtual COM port (/dev/ttyACM0):
 *
 *   # t_ms,dist_mm,echo_us,status
 *   12345,1234,7196,ok
 *
 * Lines starting with '#' are comments (banner, self-test, messages), so CSV
 * tools can skip them. status is ok, out_of_range, no_echo or busy.
 *
 * Keys: 'p' pause/resume, 'v' rerun the wiring self-test, 'i' info, 'r' reset.
 * The LED (PB3) blinks at 1 Hz while the firmware runs.
 */
#include <stdint.h>
#include "stm32f0xx.h"
#include "fw_info.h"
#include "uart.h"
#include "hcsr04.h"

#define LED_PORT      GPIOB
#define LED_PIN       3u
#define UART_BAUD     115200u
#define PERIOD_MS     100u     /* 10 Hz. The datasheet asks for >= 60 ms between measurements */

/* Divider on ECHO: 10k on top, 18k to ground. The sensor's supply is about
 * echo_mv * (10 + 18) / 18, assuming ECHO swings to the sensor's VCC. */
#define DIV_TOP_K     10u
#define DIV_BOT_K     18u

__attribute__((section(".fw_info"), used))
const fw_info_t g_fw_info = { FW_INFO_MAGIC, FW_BUILD_ID };

volatile uint32_t g_uptime_ms;

void SysTick_Handler(void)
{
    g_uptime_ms++;
}

/* 8 MHz HSI -> /2 -> PLL x12 -> 48 MHz. HSI/PREDIV is used rather than the
 * fixed HSI/2 source because CMSIS's SystemCoreClockUpdate() misreads HSI/2 on
 * the F042 and reports 96 MHz (see the hello project). */
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

static void print_banner(void)
{
    uart_puts("\n# === STM32F042K6 HC-SR04 distance stream ===\n");
    uart_puts("# build : "); uart_puts(g_fw_info.build_id); uart_puts("\n");
    uart_puts("# wiring: TRIG=A0(PA0)  ECHO=D3(PB0) via 10k/18k divider\n");
    uart_puts("# rate  : "); uart_put_dec(1000u / PERIOD_MS); uart_puts(" Hz, dist_mm = echo_us * 0.343 / 2\n");
    uart_puts("# keys  : 'p' pause/resume, 'v' wiring check, 'i' info, 'r' reset\n");
}

static void put_volts(uint32_t mv)   /* 3110 -> "3.11" */
{
    uart_put_fixed((mv + 5u) / 10u, 2);
}

/* Measure the divided ECHO voltage with the ADC and say whether it's safe and readable. */
static void print_echo_check(void)
{
    uint32_t echo_mv = 0, vdda_mv = 0;
    int saturated = 0;
    int rc = hcsr04_echo_check(&echo_mv, &vdda_mv, &saturated);

    uart_puts("# wiring check: ");
    if (rc == -1) {
        uart_puts("FAIL: no echo pulse after 3 tries. Check sensor VCC=5V and GND, TRIG on A0, ECHO divider to D3.\n");
        return;
    }
    if (rc == -2) {
        uart_puts("FAIL: ECHO stuck high for 250 ms. Check the divider and the ECHO wire.\n");
        return;
    }
    uart_puts("echo high = "); put_volts(echo_mv);
    uart_puts(" V at D3 (MCU supply "); put_volts(vdda_mv); uart_puts(" V)");
    if (saturated) {
        uart_puts("\n# wiring check: DANGER: D3 is at or above the 3.3 V rail. The divider may be missing\n"
                  "#               or miswired. Unplug USB and recheck it before running further.\n");
    } else if (echo_mv < 2000u) {
        uart_puts("\n# wiring check: WARN: lower than the expected ~3.2 V (resistors swapped?). Safe,\n"
                  "#               but readings may be unreliable. 10k goes ECHO->D3, 18k goes D3->GND.\n");
    } else {
        uart_puts(", so sensor VCC ~ ");
        put_volts(echo_mv * (DIV_TOP_K + DIV_BOT_K) / DIV_BOT_K);
        uart_puts(" V: OK\n");
    }
}

static void print_header(void)
{
    uart_puts("# t_ms,dist_mm,echo_us,status\n");
}

int main(void)
{
    clock_init();
    SysTick_Config(SystemCoreClock / 1000u);
    led_init();
    uart_init(UART_BAUD);
    hcsr04_init();

    print_banner();
    print_echo_check();
    print_header();

    int streaming = 1;
    uint32_t next_meas = g_uptime_ms, next_blink = g_uptime_ms;
    for (;;) {
        uint32_t now = g_uptime_ms;

        if ((int32_t)(now - next_blink) >= 0) {
            next_blink += 500u;
            LED_PORT->ODR ^= 1u << LED_PIN;
        }

        if (streaming && (int32_t)(now - next_meas) >= 0) {
            next_meas += PERIOD_MS;
            hcsr04_start(now);
        }

        hcsr04_result_t r;
        if (hcsr04_poll(now, &r)) {
            uart_put_dec(now);        uart_putc(',');
            uart_put_dec(r.dist_mm);  uart_putc(',');
            uart_put_dec(r.echo_us);  uart_putc(',');
            uart_puts(hcsr04_status_str(r.status));
            uart_puts("\n");
        }

        int c = uart_getc();
        if (c == 'p') {
            streaming = !streaming;
            uart_puts(streaming ? "# resumed\n" : "# paused ('p' to resume)\n");
            next_meas = g_uptime_ms;
        } else if (c == 'v') {
            print_echo_check();
            next_meas = g_uptime_ms;
        } else if (c == 'i') {
            print_banner();
            print_header();
        } else if (c == 'r') {
            uart_puts("# resetting...\n");
            uart_flush();
            NVIC_SystemReset();
        }
    }
}
