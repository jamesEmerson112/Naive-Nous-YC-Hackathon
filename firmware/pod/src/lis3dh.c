#include "lis3dh.h"
#include "i2c.h"

uint8_t g_lis3dh_addr = LIS3DH_ADDR;

int lis3dh_init(void)
{
    uint8_t id = 0;
    int rc = i2c_read(g_lis3dh_addr, LIS3DH_WHO_AM_I, &id, 1);
    if (rc) return rc;
    if (id != LIS3DH_ID) return -10;
    if ((rc = i2c_write(g_lis3dh_addr, LIS3DH_CTRL_REG1, ACC_CTRL1_VALUE))) return rc;
    if ((rc = i2c_write(g_lis3dh_addr, LIS3DH_CTRL_REG4, ACC_CTRL4_VALUE))) return rc;
    uint8_t c1, c4;
    rc = lis3dh_check_config(&c1, &c4);
    return rc < 0 ? rc : (rc ? -11 : 0);
}

int lis3dh_check_config(uint8_t *ctrl1, uint8_t *ctrl4)
{
    int rc = i2c_read(g_lis3dh_addr, LIS3DH_CTRL_REG1, ctrl1, 1);
    if (rc) return rc;
    if ((rc = i2c_read(g_lis3dh_addr, LIS3DH_CTRL_REG4, ctrl4, 1))) return rc;
    return (*ctrl1 != ACC_CTRL1_VALUE || *ctrl4 != ACC_CTRL4_VALUE) ? 1 : 0;
}

int lis3dh_read(acc_sample_t *out, int *fresh)
{
    uint8_t st = 0, b[6];
    int rc = i2c_read(g_lis3dh_addr, LIS3DH_STATUS_REG, &st, 1);
    if (rc) return rc;
    *fresh = (st & 0x08u) != 0;   /* ZYXDA: new X, Y and Z data */
    if ((rc = i2c_read(g_lis3dh_addr, LIS3DH_OUT_X_L | LIS3DH_AUTO_INC, b, 6))) return rc;
    out->x = (int16_t)((int16_t)(b[0] | (b[1] << 8)) >> 4) * ACC_MG_PER_DIGIT;
    out->y = (int16_t)((int16_t)(b[2] | (b[3] << 8)) >> 4) * ACC_MG_PER_DIGIT;
    out->z = (int16_t)((int16_t)(b[4] | (b[5] << 8)) >> 4) * ACC_MG_PER_DIGIT;
    return 0;
}
