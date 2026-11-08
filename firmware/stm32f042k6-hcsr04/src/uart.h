/* Polled USART2 on PA2 (TX) / PA15 (RX), which reach the probe's virtual COM port on a Nucleo-32. */
#ifndef UART_H
#define UART_H

#include <stdint.h>

void uart_init(uint32_t baud);       /* call after the system clock is set */
void uart_putc(char c);
void uart_puts(const char *s);       /* "\n" is sent as "\r\n" */
void uart_put_dec(uint32_t v);
void uart_put_fixed(uint32_t v, unsigned decimals);  /* uart_put_fixed(3214, 3) -> "3.214" */
void uart_put_hex(uint32_t v, int digits);
int  uart_getc(void);                /* -1 if nothing has arrived */
void uart_flush(void);               /* wait until the last byte is on the wire */

#endif
