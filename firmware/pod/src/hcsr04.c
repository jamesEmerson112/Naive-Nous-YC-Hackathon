#include "hcsr04.h"
#include "adc.h"
#include "stm32f0xx.h"

/* Pins. ECHO must stay on a TIM3_CH3-capable pin (PB0 = AF1). */
#define TRIG_PORT   GPIOA
#define TRIG_PIN    0u
#define ECHO_PORT   GPIOB
#define ECHO_PIN    0u
#define ECHO_ADC_CH ADC_CHSELR_CHSEL8   /* PB0 = ADC_IN8 */

/* Written by the TIM3 interrupt, read by hcsr04_poll(). */
static volatile uint16_t rise_ticks;
static volatile uint8_t  rise_seen;
static volatile uint32_t pulse_us;
static volatile uint8_t  pulse_done;

static uint8_t  active;          /* a measurement is in progress */
static uint32_t start_ms;
static uint8_t  report_busy;     /* hcsr04_start() found ECHO still high */

static int echo_high(void)
{
    return (ECHO_PORT->IDR >> ECHO_PIN) & 1u;
}

static void echo_pin_mode(uint32_t mode)   /* 2 = alternate function (timer), 3 = analog */
{
    ECHO_PORT->MODER = (ECHO_PORT->MODER & ~(3u << (ECHO_PIN * 2))) | (mode << (ECHO_PIN * 2));
}

static void delay_us(uint16_t us)            /* TIM3 counts at 1 MHz */
{
    uint16_t t0 = (uint16_t)TIM3->CNT;
    while ((uint16_t)(TIM3->CNT - t0) < us) {}
}

static void trig_pulse(void)
{
    TRIG_PORT->BSRR = 1u << TRIG_PIN;
    delay_us(12);                            /* datasheet: at least 10 us */
    TRIG_PORT->BRR = 1u << TRIG_PIN;
}

void hcsr04_init(void)
{
    RCC->AHBENR  |= RCC_AHBENR_GPIOAEN | RCC_AHBENR_GPIOBEN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;

    /* TRIG: push-pull output, starting low */
    TRIG_PORT->BRR   = 1u << TRIG_PIN;
    TRIG_PORT->MODER = (TRIG_PORT->MODER & ~(3u << (TRIG_PIN * 2))) | (1u << (TRIG_PIN * 2));

    /* ECHO: TIM3_CH3 (AF1). No internal pull: the 18k divider resistor already
     * pulls it low, and a ~40k pull-down in parallel would lower the voltage. */
    ECHO_PORT->PUPDR  &= ~(3u << (ECHO_PIN * 2));
    ECHO_PORT->AFR[0]  = (ECHO_PORT->AFR[0] & ~(0xFu << (ECHO_PIN * 4))) | (1u << (ECHO_PIN * 4));
    echo_pin_mode(2);

    /* TIM3: 1 MHz free-running 16-bit counter; CH3 captures both edges of ECHO. */
    TIM3->CR1   = 0;
    TIM3->PSC   = (uint16_t)(SystemCoreClock / 1000000u - 1u);
    TIM3->ARR   = 0xFFFFu;
    TIM3->CCMR2 = (1u << TIM_CCMR2_CC3S_Pos)       /* CC3 = input from TI3 */
                | (3u << TIM_CCMR2_IC3F_Pos);      /* filter: 8 samples at 48 MHz, rejects glitches < ~170 ns */
    TIM3->CCER  = TIM_CCER_CC3E | TIM_CCER_CC3P | TIM_CCER_CC3NP;  /* both edges */
    TIM3->EGR   = TIM_EGR_UG;                      /* load PSC now */
    TIM3->SR    = 0;
    TIM3->DIER  = TIM_DIER_CC3IE;
    TIM3->CR1   = TIM_CR1_CEN;

    NVIC_SetPriority(TIM3_IRQn, 0);
    NVIC_EnableIRQ(TIM3_IRQn);
}

void TIM3_IRQHandler(void)
{
    uint32_t sr = TIM3->SR;
    if (sr & TIM_SR_CC3IF) {
        uint16_t t = (uint16_t)TIM3->CCR3;   /* reading CCR3 clears CC3IF */
        /* Which edge was it? The pin level tells us: the shortest real pulse
         * (~116 us) is far longer than the interrupt latency (~1 us). */
        if (echo_high()) {
            rise_ticks = t;
            rise_seen  = 1;
        } else if (rise_seen) {
            pulse_us   = (uint16_t)(t - rise_ticks);   /* wrap-safe up to 65 ms */
            rise_seen  = 0;
            pulse_done = 1;
        }
    }
    if (sr & TIM_SR_CC3OF) TIM3->SR = ~TIM_SR_CC3OF;   /* missed an edge; the timeout handles it */
}

void hcsr04_start(uint32_t now_ms)
{
    start_ms = now_ms;
    active = 1;
    if (echo_high()) {          /* previous echo still going (some clones hold it ~200 ms) */
        report_busy = 1;
        return;
    }
    report_busy = 0;
    __disable_irq();
    rise_seen = 0;
    pulse_done = 0;
    __enable_irq();
    trig_pulse();
}

int hcsr04_poll(uint32_t now_ms, hcsr04_result_t *out)
{
    if (!active) return 0;

    out->echo_us = 0;
    out->dist_mm = 0;
    if (report_busy) {
        out->status = HCSR04_BUSY;
    } else if (pulse_done) {
        uint32_t us = pulse_us;
        out->echo_us = us;
        out->dist_mm = (us * 343u + 1000u) / 2000u;   /* us * 0.343 mm/us / 2, rounded */
        out->status  = (us < HCSR04_MIN_ECHO_US || us > HCSR04_MAX_ECHO_US) ? HCSR04_OUT_OF_RANGE : HCSR04_OK;
    } else if (now_ms - start_ms >= HCSR04_TIMEOUT_MS) {
        /* Rose but never fell within 60 ms: past max range. Never rose: no echo at all. */
        out->status = rise_seen ? HCSR04_OUT_OF_RANGE : HCSR04_NO_ECHO;
    } else {
        return 0;               /* still waiting */
    }
    active = 0;
    return 1;
}

const char *hcsr04_status_str(hcsr04_status_t s)
{
    switch (s) {
    case HCSR04_OK:           return "ok";
    case HCSR04_OUT_OF_RANGE: return "out_of_range";
    case HCSR04_NO_ECHO:      return "no_echo";
    case HCSR04_BUSY:         return "busy";
    }
    return "?";
}

/* ---- wiring self-test ---------------------------------------------------- */

static int wait_echo(int level, uint32_t timeout_us)
{
    while (timeout_us) {
        if (echo_high() == level) return 1;
        uint32_t step = timeout_us < 10u ? timeout_us : 10u;   /* fine steps: a close echo lasts only ~0.1-0.6 ms */
        delay_us((uint16_t)step);
        timeout_us -= step;
    }
    return echo_high() == level;
}

int hcsr04_echo_check(uint32_t *echo_mv, uint32_t *vdda_mv, int *saturated)
{
    int rc = -1;
    adc_init();
    TIM3->DIER &= ~TIM_DIER_CC3IE;             /* keep the capture logic out of this */

    if (!wait_echo(0, 250000u)) {              /* let a previous echo finish */
        rc = -2;
        goto out;
    }
    uint32_t vref_raw = adc_read(ADC_CHSELR_CHSEL17);
    *vdda_mv = (3300u * VREFINT_CAL + vref_raw / 2u) / vref_raw;

    for (int attempt = 0; attempt < 3 && rc != 0; attempt++) {
        trig_pulse();
        if (!wait_echo(1, 30000u)) continue;   /* the echo normally starts ~0.5 ms after TRIG */
        delay_us(20);                          /* let the divider settle */
        echo_pin_mode(3);                      /* analog, so the ADC sees the pin */
        uint32_t raw = adc_read(ECHO_ADC_CH);
        echo_pin_mode(2);
        int still_high = echo_high();          /* sample landed inside the pulse? */
        wait_echo(0, 100000u);
        if (!still_high) continue;             /* object very close; try again */
        *saturated = raw >= 4090u;
        *echo_mv = (raw * *vdda_mv + 2047u) / 4095u;
        rc = 0;
    }

out:
    __disable_irq();
    rise_seen = 0;
    pulse_done = 0;
    __enable_irq();
    TIM3->SR = 0;
    TIM3->DIER |= TIM_DIER_CC3IE;
    return rc;
}
