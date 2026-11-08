#include "uart.h"
#include "stm32f0xx.h"

#define TX_SIZE 1024u   /* powers of two */
#define RX_SIZE 128u

static uint8_t tx_buf[TX_SIZE];
static uint8_t rx_buf[RX_SIZE];
static volatile uint16_t tx_head, tx_tail;   /* head: next write (main), tail: next send (IRQ) */
static volatile uint16_t rx_head, rx_tail;   /* head: next write (IRQ), tail: next read (main) */

uart_stats_t g_uart_stats;

void uart_init(uint32_t baud)
{
    RCC->AHBENR  |= RCC_AHBENR_GPIOAEN;
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;

    GPIOA->MODER  = (GPIOA->MODER & ~((3u << (2 * 2)) | (3u << (15 * 2))))
                  | (2u << (2 * 2)) | (2u << (15 * 2));                  /* AF mode */
    GPIOA->AFR[0] = (GPIOA->AFR[0] & ~(0xFu << (2 * 4)))        | (1u << (2 * 4));         /* AF1 */
    GPIOA->AFR[1] = (GPIOA->AFR[1] & ~(0xFu << ((15 - 8) * 4))) | (1u << ((15 - 8) * 4));  /* AF1 */
    GPIOA->PUPDR  = (GPIOA->PUPDR & ~(3u << (15 * 2))) | (1u << (15 * 2)); /* RX pull-up */

    USART2->CR1 = 0;
    USART2->BRR = (SystemCoreClock + baud / 2) / baud;                  /* PCLK = SYSCLK */
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE | USART_CR1_UE;

    NVIC_SetPriority(USART2_IRQn, 1);
    NVIC_EnableIRQ(USART2_IRQn);
}

void USART2_IRQHandler(void)
{
    uint32_t isr = USART2->ISR;
    if (isr & USART_ISR_ORE) {
        USART2->ICR = USART_ICR_ORECF;
        g_uart_stats.rx_overrun++;
    }
    if (isr & USART_ISR_RXNE) {
        uint8_t c = (uint8_t)USART2->RDR;
        uint16_t next = (uint16_t)((rx_head + 1u) & (RX_SIZE - 1u));
        if (next != rx_tail) {
            rx_buf[rx_head] = c;
            rx_head = next;
            g_uart_stats.rx_bytes++;
        } else {
            g_uart_stats.rx_drop++;
        }
    }
    if ((USART2->CR1 & USART_CR1_TXEIE) && (isr & USART_ISR_TXE)) {
        if (tx_tail != tx_head) {
            USART2->TDR = tx_buf[tx_tail];
            tx_tail = (uint16_t)((tx_tail + 1u) & (TX_SIZE - 1u));
        } else {
            USART2->CR1 &= ~USART_CR1_TXEIE;
        }
    }
}

uint32_t uart_tx_free(void)
{
    return (TX_SIZE - 1u) - ((tx_head - tx_tail) & (TX_SIZE - 1u));
}

static void put_raw(uint8_t c)
{
    uint16_t next = (uint16_t)((tx_head + 1u) & (TX_SIZE - 1u));
    while (next == tx_tail) {}           /* full: the IRQ drains it */
    tx_buf[tx_head] = c;
    tx_head = next;
    g_uart_stats.tx_bytes++;
    __disable_irq();
    USART2->CR1 |= USART_CR1_TXEIE;
    __enable_irq();
}

void uart_putc(char c)
{
    if (c == '\n') put_raw('\r');
    put_raw((uint8_t)c);
}

void uart_puts(const char *s)
{
    while (*s) uart_putc(*s++);
}

void uart_put_dec(uint32_t v)
{
    char buf[10];
    int i = 0;
    do { buf[i++] = (char)('0' + v % 10u); v /= 10u; } while (v);
    while (i) uart_putc(buf[--i]);
}

void uart_put_int(int32_t v)
{
    if (v < 0) {
        uart_putc('-');
        uart_put_dec((uint32_t)(-(v + 1)) + 1u);
    } else {
        uart_put_dec((uint32_t)v);
    }
}

void uart_put_fixed(uint32_t v, unsigned decimals)
{
    uint32_t div = 1;
    for (unsigned i = 0; i < decimals; i++) div *= 10u;
    uart_put_dec(v / div);
    if (decimals) {
        uart_putc('.');
        uint32_t frac = v % div;
        for (uint32_t d = div / 10u; d; d /= 10u) {   /* keep leading zeros */
            uart_putc((char)('0' + (frac / d) % 10u));
        }
    }
}

void uart_put_hex(uint32_t v, int digits)
{
    static const char hex[] = "0123456789ABCDEF";
    while (digits--) uart_putc(hex[(v >> (digits * 4)) & 0xFu]);
}

int uart_getc(void)
{
    if (rx_tail == rx_head) return -1;
    uint8_t c = rx_buf[rx_tail];
    rx_tail = (uint16_t)((rx_tail + 1u) & (RX_SIZE - 1u));
    return c;
}

void uart_flush(void)
{
    while (tx_tail != tx_head) {}
    while (!(USART2->ISR & USART_ISR_TC)) {}
}
