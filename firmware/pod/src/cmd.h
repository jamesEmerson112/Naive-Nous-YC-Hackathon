/* Line-based command interface on USART2.
 *
 * A command is one text line. The host prefixes it with "@<id> " and the
 * firmware tags every response line "R id=<id> ..."; the last one is
 * "R id=<id> end ok=1" (or ok=0 err="..."). Without a prefix, id=0, so a
 * person can type commands in a serial monitor. "help" lists them. */
#ifndef CMD_H
#define CMD_H

#include <stdint.h>

/* Fault-injection flags, kept across resets in the .noinit record (cleared by
 * a power cycle or "inject clear"). */
#define INJ_ACC_GONE       0x01u   /* talk to the LIS3DH at a wrong address, as if it were unplugged */
#define INJ_ACC_GONE_ADDR  0x1Au

void cmd_poll(void);

#endif
