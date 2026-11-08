/* Compact once-a-second accelerometer summary for the radio link (the XIAO
 * ESP32 relays it to the base station when the serial cable isn't there).
 *
 * Frame layout, little-endian, 16 bytes:
 *   0  u8   type = 0xA1
 *   1  u32  t_ms         time of the last sample
 *   5  i16  mean_x       mg
 *   7  i16  mean_y       mg
 *   9  i16  mean_z       mg
 *  11  u16  rms_mg       vibration: RMS deviation from the mean, all axes
 *  13  u16  n            samples in this summary
 *  15  u8   crc8         CRC-8 (poly 0x07) of bytes 0..14
 * The fields are deliberately unaligned (the frame is a byte stream). */
#ifndef FRAME_H
#define FRAME_H

#include <stdint.h>
#include "lis3dh.h"

#define FRAME_LEN  16u
#define FRAME_TYPE 0xA1u

typedef struct {
    int32_t  sx, sy, sz;      /* sums */
    uint32_t sxx, syy, szz;   /* sums of squares of (sample - first sample), keeps them small */
    int16_t  x0, y0, z0;
    uint16_t n;
    uint32_t t_ms;
} acc_summary_t;

void     summary_reset(acc_summary_t *s);
void     summary_add(acc_summary_t *s, const acc_sample_t *a, uint32_t t_ms);
uint32_t frame_pack(uint8_t *buf, const acc_summary_t *s);   /* returns FRAME_LEN, 0 if no samples */

#endif
