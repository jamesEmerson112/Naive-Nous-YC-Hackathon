/* LIS3DH 3-axis accelerometer on I2C1 (address 0x18: SDO/SA0 tied to GND).
 *
 * Configuration: 100 Hz output data rate, high-resolution (12-bit) mode,
 * +-2 g full scale, block data update. In HR mode at +-2 g one digit is 1 mg
 * (LIS3DH datasheet, Table 4 "Mechanical characteristics": 1 / 2 / 4 / 12
 * mg/digit at +-2 / 4 / 8 / 16 g). Samples are left-justified 16-bit, so a
 * 12-bit reading is the raw value >> 4.
 *
 * After its own power-on reset the LIS3DH comes up in power-down mode
 * (CTRL_REG1 = 0x07) and stops producing new samples, so the firmware reads
 * the configuration back once a second and restores it if it changed. */
#ifndef LIS3DH_H
#define LIS3DH_H

#include <stdint.h>

#define LIS3DH_ADDR        0x18u
#define LIS3DH_WHO_AM_I    0x0Fu   /* reads 0x33 */
#define LIS3DH_CTRL_REG1   0x20u
#define LIS3DH_CTRL_REG2   0x21u
#define LIS3DH_CTRL_REG3   0x22u
#define LIS3DH_CTRL_REG4   0x23u
#define LIS3DH_CTRL_REG5   0x24u
#define LIS3DH_CTRL_REG6   0x25u
#define LIS3DH_STATUS_REG  0x27u
#define LIS3DH_OUT_X_L     0x28u
#define LIS3DH_AUTO_INC    0x80u   /* set in the register address to read several registers */
#define LIS3DH_ID          0x33u

/* CTRL_REG1: ODR[7:4] LPen[3] Zen Yen Xen */
#define CTRL1_ODR_100HZ    0x50u
#define CTRL1_ODR_400HZ    0x70u
#define CTRL1_XYZ_EN       0x07u
/* CTRL_REG4: BDU[7] BLE[6] FS[5:4] HR[3] ST[2:1] SIM[0] */
#define CTRL4_BDU          0x80u
#define CTRL4_HR           0x08u
#define CTRL4_FS_2G        0x00u
#define CTRL4_FS_4G        0x10u

#define ACC_ODR            CTRL1_ODR_100HZ
#define ACC_CTRL1_VALUE    (ACC_ODR | CTRL1_XYZ_EN)
#define ACC_CTRL4_VALUE    (CTRL4_BDU | CTRL4_HR | CTRL4_FS_2G)
#define ACC_MG_PER_DIGIT   1        /* HR mode, +-2 g */

typedef struct {
    int16_t x, y, z;   /* mg */
} acc_sample_t;

int lis3dh_init(void);                                   /* 0, or an I2C error / -10 wrong WHO_AM_I */
int lis3dh_read(acc_sample_t *out, int *fresh);          /* fresh: a new sample was ready */
int lis3dh_check_config(uint8_t *ctrl1, uint8_t *ctrl4); /* 0 = matches, 1 = changed, <0 = I2C error */

extern uint8_t g_lis3dh_addr;   /* normally LIS3DH_ADDR; `inject acc_gone` points it elsewhere */

#endif
