#include "adc.h"
#include "stm32f0xx.h"

void adc_init(void)
{
    static int done;
    if (done) return;
    done = 1;

    RCC->APB2ENR |= RCC_APB2ENR_ADCEN;
    ADC1->CFGR2 = ADC_CFGR2_CKMODE_1;          /* ADC clock = PCLK/4 = 12 MHz (max 14) */
    if (ADC1->CR & ADC_CR_ADEN) {              /* calibration needs the ADC off */
        ADC1->CR |= ADC_CR_ADDIS;
        while (ADC1->CR & ADC_CR_ADEN) {}
    }
    ADC1->CR = ADC_CR_ADCAL;
    while (ADC1->CR & ADC_CR_ADCAL) {}
    ADC1->SMPR = ADC_SMPR_SMP;                 /* 239.5 cycles (~20 us); the temp sensor needs >= 4 us */
    ADC->CCR |= ADC_CCR_VREFEN | ADC_CCR_TSEN;
    ADC1->ISR = ADC_ISR_ADRDY;
    ADC1->CR |= ADC_CR_ADEN;
    while (!(ADC1->ISR & ADC_ISR_ADRDY)) {}
}

uint32_t adc_read(uint32_t chsel)
{
    ADC1->CHSELR = chsel;
    ADC1->CR |= ADC_CR_ADSTART;
    while (!(ADC1->ISR & ADC_ISR_EOC)) {}
    return ADC1->DR;                           /* reading DR clears EOC */
}

uint32_t adc_vdda_mv(void)
{
    uint32_t raw = adc_read(ADC_CHSELR_CHSEL17);
    if (!raw) return 0;
    return (3300u * VREFINT_CAL + raw / 2u) / raw;
}

int32_t adc_temp_c10(uint32_t vdda_mv)
{
    int32_t ts_mv  = (int32_t)((adc_read(ADC_CHSELR_CHSEL16) * vdda_mv) / 4095u);
    int32_t cal_mv = (int32_t)((TS_CAL1 * 3300u) / 4095u);
    return 300 + ((cal_mv - ts_mv) * 100) / 43;
}
