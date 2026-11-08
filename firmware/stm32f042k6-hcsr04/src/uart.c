#include "uart.h"
#include "stm32f0xx.h"

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
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

void uart_putc(char c)
{
    while (!(USART2->ISR & USART_ISR_TXE)) {}
    USART2->TDR = (uint8_t)c;
}

void uart_puts(const char *s)
{
    while (*s) {
        if (*s == '\n') uart_putc('\r');
        uart_putc(*s++);
    }
}

void uart_put_dec(uint32_t v)
{
    char buf[10];
    int i = 0;
    do { buf[i++] = (char)('0' + v % 10u); v /= 10u; } while (v);
    while (i) uart_putc(buf[--i]);
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
    uint32_t isr = USART2->ISR;
    if (isr & USART_ISR_ORE) USART2->ICR = USART_ICR_ORECF;   /* an overrun blocks RX until cleared */
    if (isr & USART_ISR_RXNE) return (int)(USART2->RDR & 0xFFu);
    return -1;
}

void uart_flush(void)
{
    while (!(USART2->ISR & USART_ISR_TC)) {}
}
