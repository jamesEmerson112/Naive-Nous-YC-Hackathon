/* State shared between the main loop (main.c) and the command handlers (cmd.c). */
#ifndef APP_H
#define APP_H

#include <stdint.h>
#include "hcsr04.h"

#define ACC_STREAM_HZ   50u     /* accelerometer samples read and streamed per second */
#define US_STREAM_HZ    10u     /* the HC-SR04 datasheet asks for >= 60 ms between pings */

typedef enum { ACC_OFF = 0, ACC_OK, ACC_STALE, ACC_ERR } acc_state_t;

typedef struct {
    uint32_t acc_period_ms;
    uint32_t us_period_ms;
    uint8_t  stream_a, stream_d, stream_f;
    uint8_t  sensors_on;
    acc_state_t acc_state;
    int32_t  acc_err;           /* last accelerometer error (i2c.h codes, -10 bad id, -11 config didn't stick) */
    uint32_t acc_errors;
    uint32_t acc_cfg_lost;      /* times the config readback found the LIS3DH reset */
    uint32_t acc_reinit;
    uint32_t acc_stale_run;     /* consecutive reads with no new sample */
    hcsr04_status_t us_last;
    uint32_t loop_max_us;       /* longest main-loop pass since the last health record */
    uint32_t vdda_mv;
    int32_t  temp_c10;
} app_t;

extern app_t g_app;
extern volatile uint32_t g_uptime_ms;

void        app_sensors_start(void);
void        app_sensors_stop(void);
int         app_acc_reinit(const char *why);
void        app_emit_health(void);
const char *app_acc_state_str(void);

#endif
