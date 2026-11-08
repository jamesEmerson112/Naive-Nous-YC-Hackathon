#include "i2c.h"
#include "stm32f0xx.h"

#define SCL_PIN 6u
#define SDA_PIN 7u

/* 100 kHz with the I2C kernel clock = HSI 8 MHz (the reset default, set in
 * RCC_CFGR3.I2C1SW), from the RM0091 timing table: PRESC=1 SCLDEL=4 SDADEL=2
 * SCLH=0x0F SCLL=0x13. Independent of SYSCLK. */
#define I2C_TIMING_100K 0x10420F13u

extern volatile uint32_t g_uptime_ms;
i2c_stats_t g_i2c_stats;

static void pins_mode(uint32_t mode)   /* 1 = GPIO output, 2 = alternate function */
{
    GPIOB->MODER = (GPIOB->MODER & ~((3u << (SCL_PIN * 2)) | (3u << (SDA_PIN * 2))))
                 | (mode << (SCL_PIN * 2)) | (mode << (SDA_PIN * 2));
}

static void spin_us(uint32_t us)       /* rough: ~8 cycles per iteration at 48 MHz */
{
    for (volatile uint32_t i = 0; i < us * 6u; i++) {}
}

uint32_t i2c_lines(void)
{
    uint32_t idr = GPIOB->IDR;
    return ((idr >> SCL_PIN) & 1u) | (((idr >> SDA_PIN) & 1u) << 1);
}

int i2c_bus_recover(void)
{
    I2C1->CR1 &= ~I2C_CR1_PE;
    GPIOB->BSRR = (1u << SCL_PIN) | (1u << SDA_PIN);   /* open-drain, released */
    pins_mode(1);
    spin_us(5);
    /* A slave left mid-byte is waiting for clocks. Give it up to 9 so it
     * finishes the byte and the (N)ACK slot, then send a STOP. */
    for (int i = 0; i < 9 && !(GPIOB->IDR & (1u << SDA_PIN)); i++) {
        GPIOB->BRR  = 1u << SCL_PIN; spin_us(5);
        GPIOB->BSRR = 1u << SCL_PIN; spin_us(5);
    }
    GPIOB->BRR  = 1u << SCL_PIN; spin_us(5);
    GPIOB->BRR  = 1u << SDA_PIN; spin_us(5);
    GPIOB->BSRR = 1u << SCL_PIN; spin_us(5);
    GPIOB->BSRR = 1u << SDA_PIN; spin_us(5);
    int sda_high = (GPIOB->IDR >> SDA_PIN) & 1u;
    pins_mode(2);
    I2C1->CR1 |= I2C_CR1_PE;
    g_i2c_stats.recover++;
    return sda_high;
}

void i2c_init(void)
{
    RCC->AHBENR  |= RCC_AHBENR_GPIOBEN;
    RCC->APB1ENR |= RCC_APB1ENR_I2C1EN;

    GPIOB->OTYPER |= (1u << SCL_PIN) | (1u << SDA_PIN);                    /* open-drain */
    GPIOB->PUPDR  &= ~((3u << (SCL_PIN * 2)) | (3u << (SDA_PIN * 2)));     /* breakout has pull-ups */
    GPIOB->OSPEEDR &= ~((3u << (SCL_PIN * 2)) | (3u << (SDA_PIN * 2)));
    GPIOB->AFR[0] = (GPIOB->AFR[0] & ~((0xFu << (SCL_PIN * 4)) | (0xFu << (SDA_PIN * 4))))
                  | (1u << (SCL_PIN * 4)) | (1u << (SDA_PIN * 4));          /* AF1 = I2C1 */

    I2C1->CR1 = 0;
    I2C1->TIMINGR = I2C_TIMING_100K;
    pins_mode(2);
    I2C1->CR1 = I2C_CR1_PE;

    /* If the MCU reset in the middle of a read, the slave may still be
     * holding SDA low. Free the bus before the first transfer. */
    if ((i2c_lines() & 2u) == 0) i2c_bus_recover();
}

static int fail(int err)
{
    switch (err) {
    case I2C_ERR_NACK:    g_i2c_stats.nack++;    break;
    case I2C_ERR_TIMEOUT: g_i2c_stats.timeout++; break;
    case I2C_ERR_BUS:     g_i2c_stats.bus++;     break;
    case I2C_ERR_ARLO:    g_i2c_stats.arlo++;    break;
    }
    g_i2c_stats.last_err = err;

    /* Leave the peripheral idle for the next transfer: send STOP if the bus
     * is still ours, clear the flags, and if it's still busy, reset it
     * (clearing PE resets the I2C state machine; RM0091 "Software reset"). */
    if (I2C1->ISR & I2C_ISR_BUSY) {
        I2C1->CR2 |= I2C_CR2_STOP;
        uint32_t t0 = g_uptime_ms;
        while ((I2C1->ISR & I2C_ISR_BUSY) && (g_uptime_ms - t0) <= I2C_TIMEOUT_MS) {}
    }
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
    if (I2C1->ISR & I2C_ISR_BUSY) {
        I2C1->CR1 &= ~I2C_CR1_PE;
        while (I2C1->CR1 & I2C_CR1_PE) {}
        I2C1->CR1 |= I2C_CR1_PE;
    }
    return err;
}

/* Wait for an ISR flag, giving up on NACK, bus error, arbitration loss or timeout. */
static int wait_isr(uint32_t flag)
{
    uint32_t t0 = g_uptime_ms;
    for (;;) {
        uint32_t isr = I2C1->ISR;
        if (isr & flag)           return I2C_OK;
        if (isr & I2C_ISR_NACKF)  return I2C_ERR_NACK;
        if (isr & I2C_ISR_BERR)   return I2C_ERR_BUS;
        if (isr & I2C_ISR_ARLO)   return I2C_ERR_ARLO;
        if (g_uptime_ms - t0 > I2C_TIMEOUT_MS) return I2C_ERR_TIMEOUT;
    }
}

static int finish(void)
{
    int rc = wait_isr(I2C_ISR_STOPF);
    if (rc) return fail(rc);
    I2C1->ICR = I2C_ICR_STOPCF;
    g_i2c_stats.ok++;
    return I2C_OK;
}

int i2c_read(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t n)
{
    int rc;
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
    I2C1->CR2 = ((uint32_t)addr << 1) | (1u << I2C_CR2_NBYTES_Pos) | I2C_CR2_START;
    if ((rc = wait_isr(I2C_ISR_TXIS))) return fail(rc);
    I2C1->TXDR = reg;
    if ((rc = wait_isr(I2C_ISR_TC))) return fail(rc);

    I2C1->CR2 = ((uint32_t)addr << 1) | I2C_CR2_RD_WRN | ((uint32_t)n << I2C_CR2_NBYTES_Pos)
              | I2C_CR2_AUTOEND | I2C_CR2_START;
    for (uint8_t i = 0; i < n; i++) {
        if ((rc = wait_isr(I2C_ISR_RXNE))) return fail(rc);
        buf[i] = (uint8_t)I2C1->RXDR;
    }
    return finish();
}

int i2c_write(uint8_t addr, uint8_t reg, uint8_t val)
{
    int rc;
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
    I2C1->CR2 = ((uint32_t)addr << 1) | (2u << I2C_CR2_NBYTES_Pos) | I2C_CR2_AUTOEND | I2C_CR2_START;
    if ((rc = wait_isr(I2C_ISR_TXIS))) return fail(rc);
    I2C1->TXDR = reg;
    if ((rc = wait_isr(I2C_ISR_TXIS))) return fail(rc);
    I2C1->TXDR = val;
    return finish();
}

int i2c_probe(uint8_t addr)
{
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
    I2C1->CR2 = ((uint32_t)addr << 1) | I2C_CR2_AUTOEND | I2C_CR2_START;   /* 0 bytes */
    uint32_t t0 = g_uptime_ms;
    while (!(I2C1->ISR & I2C_ISR_STOPF)) {
        if (g_uptime_ms - t0 > I2C_TIMEOUT_MS) return fail(I2C_ERR_TIMEOUT);
    }
    int nack = (I2C1->ISR & I2C_ISR_NACKF) != 0;
    I2C1->ICR = I2C_ICR_NACKCF | I2C_ICR_STOPCF;
    return nack ? I2C_ERR_NACK : I2C_OK;
}

const char *i2c_err_str(int err)
{
    switch (err) {
    case I2C_OK:          return "ok";
    case I2C_ERR_NACK:    return "nack";
    case I2C_ERR_TIMEOUT: return "timeout";
    case I2C_ERR_BUS:     return "bus_error";
    case I2C_ERR_ARLO:    return "arbitration_lost";
    }
    return "?";
}
