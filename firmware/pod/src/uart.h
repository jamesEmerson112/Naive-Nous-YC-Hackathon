/* Interrupt-driven USART2 on PA2 (TX) / PA15 (RX), which reach the probe's
 * virtual COM port on a Nucleo-32 (/dev/ttyACM0).
 *
 * TX and RX each go through a ring buffer serviced by USART2_IRQHandler, so
 * printing never stalls the main loop unless the TX ring is full. At 115200
 * baud (8N1) the link carries at most 11520 bytes/s; telemetry that produces
 * more than that fills the ring, and droppable lines are then skipped (see
 * tl.h) and counted in g_uart_stats.tx_drop_lines. */
#ifndef UART_H
#define UART_H

#include <stdint.h>

typedef struct {
    uint32_t tx_bytes;
    uint32_t tx_drop_lines;   /* droppable telemetry lines skipped because the TX ring was full */
    uint32_t rx_bytes;
    uint32_t rx_overrun;      /* hardware overrun (byte lost before the IRQ read it) */
    uint32_t rx_drop;         /* RX ring full */
} uart_stats_t;

extern uart_stats_t g_uart_stats;

void     uart_init(uint32_t baud);    /* call after the system clock is set */
void     uart_putc(char c);           /* blocks while the TX ring is full */
void     uart_puts(const char *s);    /* "\n" is sent as "\r\n" */
void     uart_put_dec(uint32_t v);
void     uart_put_int(int32_t v);
void     uart_put_fixed(uint32_t v, unsigned decimals);  /* (3214, 3) -> "3.214" */
void     uart_put_hex(uint32_t v, int digits);
uint32_t uart_tx_free(void);          /* free bytes in the TX ring */
int      uart_getc(void);             /* -1 if nothing has arrived */
void     uart_flush(void);            /* wait until the last byte is on the wire */

#endif
