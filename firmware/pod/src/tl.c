#include "tl.h"
#include "uart.h"

#define TL_MAX_LINE 112u      /* longest droppable line, with margin */

static uint8_t skip;          /* current droppable line didn't fit */
uint32_t g_tl_resp_id;

void tl_begin(char type)
{
    skip = 0;
    uart_putc(type);
}

void tl_begin_drop(char type)
{
    if (uart_tx_free() < TL_MAX_LINE) {
        skip = 1;
        g_uart_stats.tx_drop_lines++;
        return;
    }
    skip = 0;
    uart_putc(type);
}

static void key(const char *k)
{
    uart_putc(' ');
    uart_puts(k);
    uart_putc('=');
}

void tl_u(const char *k, uint32_t v)
{
    if (skip) return;
    key(k);
    uart_put_dec(v);
}

void tl_i(const char *k, int32_t v)
{
    if (skip) return;
    key(k);
    uart_put_int(v);
}

void tl_x(const char *k, uint32_t v, int digits)
{
    if (skip) return;
    key(k);
    uart_puts("0x");
    uart_put_hex(v, digits);
}

void tl_s(const char *k, const char *s)
{
    if (skip) return;
    key(k);
    uart_puts(s);
}

void tl_q(const char *k, const char *s)
{
    if (skip) return;
    key(k);
    uart_putc('"');
    uart_puts(s);
    uart_putc('"');
}

void tl_fixed(const char *k, int32_t v, unsigned decimals)
{
    if (skip) return;
    key(k);
    if (v < 0) {
        uart_putc('-');
        v = -v;
    }
    uart_put_fixed((uint32_t)v, decimals);
}

void tl_hex_bytes(const char *k, const uint8_t *p, uint32_t n)
{
    if (skip) return;
    key(k);
    while (n--) uart_put_hex(*p++, 2);
}

void tl_end(void)
{
    if (!skip) uart_putc('\n');
    skip = 0;
}

void tl_resp_begin(void)
{
    tl_begin('R');
    tl_u("id", g_tl_resp_id);
}
