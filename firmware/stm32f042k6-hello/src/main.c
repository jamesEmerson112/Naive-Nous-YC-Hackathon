/* STM32F042K6 sanity firmware.
 *
 * Shows the code built on this machine is what runs on the board:
 *   - blinks the LED (PB3, LD3 on a Nucleo-F042K6) at 1 Hz
 *   - prints a boot banner with the build ID, chip UID and reset cause on
 *     USART2 (PA2 TX / PA15 RX, 115200 8N1). On a Nucleo-32 that reaches the
 *     probe's virtual COM port, which is /dev/ttyACM0 here
 *   - prints a heartbeat every second and echoes what you type
 *     ('i' reprints the banner, 'r' resets the chip)
 *   - keeps g_fw_info (the build ID) and g_uptime_ms where `make check` can read them over SWD
 *
 * Bare metal on the CMSIS register headers: no HAL, no printf.
 */
#include <stdint.h>
#include "stm32f0xx.h"
#include "fw_info.h"

/* ---- board configuration (change these for a different board) ---------- */
#define LED_PORT      GPIOB
#define LED_PIN       3u
#define LED_PORT_EN   RCC_AHBENR_GPIOBEN
#define UART_BAUD     115200u

/* ---- build ID and uptime, read by `make check` ------------------------- */
__attribute__((section(".fw_info"), used))
const fw_info_t g_fw_info = { FW_INFO_MAGIC, FW_BUILD_ID };

volatile uint32_t g_uptime_ms;

void SysTick_Handler(void)
{
    g_uptime_ms++;
}

/* ---- clocks: 8 MHz HSI -> /2 -> PLL x12 -> 48 MHz SYSCLK ---------------- */
static void clock_init(void)
{
    /* If something (a bootloader, say) left the PLL running, move off it first. */
    if ((RCC->CFGR & RCC_CFGR_SWS) == RCC_CFGR_SWS_PLL) {
        RCC->CFGR &= ~RCC_CFGR_SW;                      /* SW = HSI */
        while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_HSI) {}
    }
    RCC->CR &= ~RCC_CR_PLLON;
    while (RCC->CR & RCC_CR_PLLRDY) {}

    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY;  /* 1 wait state above 24 MHz */

    /* Use HSI/PREDIV with PREDIV = 2 rather than the fixed HSI/2 source. The
     * hardware result is the same, but CMSIS's SystemCoreClockUpdate() reads
     * PLLSRC=HSI/2 on the F042 as HSI/PREDIV and would report 96 MHz, which
     * doubles the UART baud and SysTick math. (Measured on the board.) */
    RCC->CFGR2 = RCC_CFGR2_PREDIV_DIV2;
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_PLLSRC | RCC_CFGR_PLLMUL | RCC_CFGR_HPRE | RCC_CFGR_PPRE))
              | RCC_CFGR_PLLSRC_HSI_PREDIV | RCC_CFGR_PLLMUL12;  /* 8/2 x 12 = 48 MHz; AHB, APB undivided */
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY)) {}

    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) {}

    SystemCoreClockUpdate();                            /* SystemCoreClock = 48000000 */
}

/* ---- GPIO ---------------------------------------------------------------- */
static void led_init(void)
{
    RCC->AHBENR |= LED_PORT_EN;
    LED_PORT->MODER = (LED_PORT->MODER & ~(3u << (LED_PIN * 2))) | (1u << (LED_PIN * 2)); /* output */
}

static void led_toggle(void)
{
    LED_PORT->ODR ^= (1u << LED_PIN);
}

/* ---- USART2 on PA2 (TX) / PA15 (RX), alternate function 1 --------------- */
static void uart_init(void)
{
    RCC->AHBENR  |= RCC_AHBENR_GPIOAEN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;

    GPIOA->MODER  = (GPIOA->MODER & ~((3u << (2 * 2)) | (3u << (15 * 2))))
                  | (2u << (2 * 2)) | (2u << (15 * 2));                  /* AF mode */
    GPIOA->AFR[0] = (GPIOA->AFR[0] & ~(0xFu << (2 * 4)))        | (1u << (2 * 4));
    GPIOA->AFR[1] = (GPIOA->AFR[1] & ~(0xFu << ((15 - 8) * 4))) | (1u << ((15 - 8) * 4));
    GPIOA->PUPDR  = (GPIOA->PUPDR & ~(3u << (15 * 2))) | (1u << (15 * 2)); /* RX pull-up */

    USART2->CR1 = 0;
    USART2->BRR = (SystemCoreClock + UART_BAUD / 2) / UART_BAUD;        /* PCLK = SYSCLK */
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void uart_putc(char c)
{
    while (!(USART2->ISR & USART_ISR_TXE)) {}
    USART2->TDR = (uint8_t)c;
}

static void uart_puts(const char *s)
{
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

static void uart_put_dec(uint32_t v)
{
    char buf[11];
    int i = 0;
    do { buf[i++] = (char)('0' + v % 10u); v /= 10u; } while (v);
    while (i) uart_putc(buf[--i]);
}

static void uart_put_hex(uint32_t v, int digits)
{
    static const char hex[] = "0123456789ABCDEF";
    while (digits--) uart_putc(hex[(v >> (digits * 4)) & 0xFu]);
}

static int uart_getc(void)   /* -1 if nothing has arrived */
{
    uint32_t isr = USART2->ISR;
    if (isr & USART_ISR_ORE) USART2->ICR = USART_ICR_ORECF;   /* a full RX latches the port until cleared */
    if (isr & USART_ISR_RXNE) return (int)(USART2->RDR & 0xFFu);
    return -1;
}

/* ---- boot banner -------------------------------------------------------- */
static uint32_t reset_flags;   /* RCC->CSR at boot (the flags are cleared afterwards) */

static void print_banner(void)
{
    const uint32_t *uid = (const uint32_t *)UID_BASE;

    uart_puts("\n=== STM32F042K6 sanity firmware ===\n");
    uart_puts("build : "); uart_puts(g_fw_info.build_id); uart_puts("\n");
    uart_puts("chip  : dev 0x"); uart_put_hex(DBGMCU->IDCODE & 0xFFFu, 3);
    uart_puts(" rev 0x");        uart_put_hex(DBGMCU->IDCODE >> 16, 4);
    uart_puts(", ");             uart_put_dec(*(const uint16_t *)FLASHSIZE_BASE);
    uart_puts(" KB flash\n");
    uart_puts("uid   : ");
    uart_put_hex(uid[2], 8); uart_putc('-'); uart_put_hex(uid[1], 8); uart_putc('-'); uart_put_hex(uid[0], 8);
    uart_puts("\nclock : "); uart_put_dec(SystemCoreClock); uart_puts(" Hz\n");
    uart_puts("reset :");
    if (reset_flags & RCC_CSR_PORRSTF)  uart_puts(" power-on");
    if (reset_flags & RCC_CSR_PINRSTF)  uart_puts(" pin");
    if (reset_flags & RCC_CSR_SFTRSTF)  uart_puts(" software");
    if (reset_flags & RCC_CSR_IWDGRSTF) uart_puts(" iwdg");
    if (reset_flags & RCC_CSR_WWDGRSTF) uart_puts(" wwdg");
    if (reset_flags & RCC_CSR_LPWRRSTF) uart_puts(" low-power");
    if (reset_flags & RCC_CSR_OBLRSTF)  uart_puts(" option-byte-load");
    uart_puts("\nkeys  : 'i' info, 'r' reset, anything else echoes\n\n");
}

int main(void)
{
    reset_flags = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF;

    clock_init();
    RCC->APB2ENR |= RCC_APB2ENR_DBGMCUEN;   /* for reading DBGMCU->IDCODE */
    SysTick_Config(SystemCoreClock / 1000u);  /* 1 ms tick */
    led_init();
    uart_init();
    print_banner();

    uint32_t next_blink = 0, beats = 0, rx_count = 0;
    for (;;) {
        uint32_t now = g_uptime_ms;

        if ((int32_t)(now - next_blink) >= 0) {   /* every 500 ms, wrap-safe */
            next_blink += 500u;
            led_toggle();
            if ((++beats & 1u) == 0) {
                uart_puts("alive: uptime ");
                uart_put_dec(now / 1000u); uart_putc('.'); uart_put_dec((now / 100u) % 10u);
                uart_puts(" s, rx ");
                uart_put_dec(rx_count);
                uart_puts(" bytes\n");
            }
        }

        int c = uart_getc();
        if (c >= 0) {
            rx_count++;
            if (c == 'i') {
                print_banner();
            } else if (c == 'r') {
                uart_puts("\nresetting...\n");
                while (!(USART2->ISR & USART_ISR_TC)) {}
                NVIC_SystemReset();
            } else {
                uart_puts("echo: ");
                if (c == '\r' || c == '\n') uart_puts("<enter>");
                else if (c >= 0x20 && c < 0x7f) uart_putc((char)c);
                else { uart_puts("0x"); uart_put_hex((uint32_t)c, 2); }
                uart_puts("\n");
            }
        }
    }
}
