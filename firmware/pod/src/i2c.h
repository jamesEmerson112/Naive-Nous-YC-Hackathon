/* I2C1 master, 100 kHz, on PB6 (SCL, header D5) and PB7 (SDA, header D4).
 *
 * On the Nucleo-32, header pins A5/A4 (PA6/PA5) are tied to PB6/PB7 by
 * solder bridges SB16/SB18, so a sensor wired to A4/A5 works too, but then
 * PA5/PA6 must stay inputs: never drive them, use them for the ADC, or give
 * them to TIM3. The LIS3DH breakout provides the pull-ups.
 *
 * Every wait has a timeout, so a missing or stuck device returns an error
 * instead of hanging the main loop. */
#ifndef I2C_H
#define I2C_H

#include <stdint.h>

#define I2C_TIMEOUT_MS 5u

enum {
    I2C_OK = 0,
    I2C_ERR_NACK = -1,      /* nobody answered: device absent, wrong address, or a wire is off */
    I2C_ERR_TIMEOUT = -2,   /* a flag never came: bus stuck (SDA or SCL held low) */
    I2C_ERR_BUS = -3,       /* misplaced START/STOP (noise, a bad contact) */
    I2C_ERR_ARLO = -4,      /* arbitration lost: something else drove SDA */
};

typedef struct {
    uint32_t ok, nack, timeout, bus, arlo, recover;
    int32_t  last_err;
} i2c_stats_t;

extern i2c_stats_t g_i2c_stats;

void        i2c_init(void);
int         i2c_read(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t n);
int         i2c_write(uint8_t addr, uint8_t reg, uint8_t val);
int         i2c_probe(uint8_t addr);      /* 0 if a device ACKs its address */
int         i2c_bus_recover(void);        /* 9 clocks + STOP; returns 1 if SDA is high afterwards */
uint32_t    i2c_lines(void);              /* bit0 = SCL level, bit1 = SDA level */
const char *i2c_err_str(int err);

#endif
