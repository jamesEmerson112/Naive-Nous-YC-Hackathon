#include "frame.h"

void summary_reset(acc_summary_t *s)
{
    *s = (acc_summary_t){0};
}

void summary_add(acc_summary_t *s, const acc_sample_t *a, uint32_t t_ms)
{
    if (s->n == 0) {
        s->x0 = a->x;
        s->y0 = a->y;
        s->z0 = a->z;
    }
    int32_t dx = a->x - s->x0, dy = a->y - s->y0, dz = a->z - s->z0;
    s->sx += dx;
    s->sy += dy;
    s->sz += dz;
    s->sxx += (uint32_t)(dx * dx);
    s->syy += (uint32_t)(dy * dy);
    s->szz += (uint32_t)(dz * dz);
    s->n++;
    s->t_ms = t_ms;
}

static uint32_t isqrt(uint32_t v)
{
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; }
        else r >>= 1;
        bit >>= 2;
    }
    return r;
}

static uint8_t crc8(const uint8_t *p, uint32_t n)
{
    uint8_t c = 0;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (uint8_t)((c & 0x80u) ? ((unsigned)c << 1) ^ 0x07u : (unsigned)c << 1);
    }
    return c;
}

static void put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t axis_var(uint32_t sum_sq, int32_t mean, int32_t n)   /* E[d^2] - E[d]^2, never negative */
{
    uint32_t a = sum_sq / (uint32_t)n, b = (uint32_t)(mean * mean);
    return a > b ? a - b : 0u;
}

uint32_t frame_pack(uint8_t *buf, const acc_summary_t *s)
{
    if (!s->n) return 0;
    int32_t n = s->n;
    int32_t mx = s->sx / n, my = s->sy / n, mz = s->sz / n;   /* mean offsets from the first sample */
    uint32_t var = axis_var(s->sxx, mx, n) + axis_var(s->syy, my, n) + axis_var(s->szz, mz, n);

    buf[0] = FRAME_TYPE;
    put_u32(buf + 1, s->t_ms);
    put_u16(buf + 5, (uint16_t)(int16_t)(s->x0 + mx));
    put_u16(buf + 7, (uint16_t)(int16_t)(s->y0 + my));
    put_u16(buf + 9, (uint16_t)(int16_t)(s->z0 + mz));
    put_u16(buf + 11, (uint16_t)isqrt(var));
    put_u16(buf + 13, s->n);
    buf[15] = crc8(buf, 15);
    return FRAME_LEN;
}
