/* ADC1 helpers: VDDA from the internal reference, die temperature, and raw
 * channel reads (the HC-SR04 wiring self-test uses PB0 = ADC_IN8). */
#ifndef ADC_H
#define ADC_H

#include <stdint.h>

/* Factory calibration values, taken at VDDA = 3.3 V (STM32F042 datasheet,
 * "Embedded internal reference voltage" and "Temperature sensor"). */
#define VREFINT_CAL (*(const uint16_t *)0x1FFFF7BAu)
#define TS_CAL1     (*(const uint16_t *)0x1FFFF7B8u)   /* temperature sensor at 30 C */

void     adc_init(void);
uint32_t adc_read(uint32_t chselr);       /* one conversion, 12-bit */
uint32_t adc_vdda_mv(void);
int32_t  adc_temp_c10(uint32_t vdda_mv);  /* die temperature in 0.1 C (typ. slope 4.3 mV/C) */

#endif
