/* HC-SR04 ultrasonic distance sensor driver.
 *
 * Wiring (Nucleo-F042K6):
 *   TRIG -> A0 (PA0), driven as a 3.3 V push-pull output
 *   ECHO -> 10k / 18k divider (about 3.2 V) -> D3 (PB0), captured by TIM3_CH3
 *
 * How it measures: a 12 us pulse on TRIG starts a measurement, and the sensor
 * holds ECHO high for as long as the sound takes to come back. TIM3 runs at
 * 1 MHz and timestamps both ECHO edges in hardware, so the pulse width is exact
 * to 1 us however busy the CPU is.
 *   distance = echo_us * 343 m/s / 2   (speed of sound at 20 C; ~0.17 mm per us)
 */
#ifndef HCSR04_H
#define HCSR04_H

#include <stdint.h>

#define HCSR04_MIN_ECHO_US    116u     /* ~2 cm: the sensor's rated minimum */
#define HCSR04_MAX_ECHO_US    23300u   /* ~400 cm: the rated maximum. No object gives ~38 ms */
#define HCSR04_TIMEOUT_MS     60u      /* give up on a measurement after this long */

typedef enum {
    HCSR04_OK = 0,
    HCSR04_OUT_OF_RANGE,   /* echo came back, but outside 2-400 cm (often: nothing in front) */
    HCSR04_NO_ECHO,        /* ECHO never went high: check power, TRIG and ECHO wiring */
    HCSR04_BUSY,           /* ECHO still high from the previous measurement */
} hcsr04_status_t;

typedef struct {
    hcsr04_status_t status;
    uint32_t echo_us;      /* ECHO pulse width (0 if none) */
    uint32_t dist_mm;      /* distance from echo_us (0 if none) */
} hcsr04_result_t;

void hcsr04_init(void);                         /* needs SystemCoreClock set */
void hcsr04_start(uint32_t now_ms);             /* fire TRIG; non-blocking */
int  hcsr04_poll(uint32_t now_ms, hcsr04_result_t *out);  /* 1 once a started measurement finishes */
const char *hcsr04_status_str(hcsr04_status_t s);

/* Wiring self-test: fire TRIG, and while ECHO is high, read the voltage on D3
 * with the ADC. Blocks for up to ~150 ms. Returns 0 if a level was measured,
 * -1 if no echo pulse arrived, -2 if ECHO was stuck high.
 *   echo_mv:   voltage at D3 while ECHO is high (the divider output)
 *   vdda_mv:   the MCU's own supply, measured against its internal reference
 *   saturated: 1 if the ADC hit full scale, meaning D3 is at or above VDDA */
int hcsr04_echo_check(uint32_t *echo_mv, uint32_t *vdda_mv, int *saturated);

#endif
