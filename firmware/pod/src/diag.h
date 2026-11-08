/* Self-diagnostics: reset cause, boot-loop detection and safe mode, crash
 * capture, watchdog, checkpoints, stack high-water mark, timing.
 *
 * State that must survive a reset lives in a .noinit RAM record (validated by
 * magic + checksum). RAM is lost on power-off, so after a power cycle the
 * record starts fresh.
 *
 * Crash capture: the Cortex-M0 has no fault-status registers (no CFSR/HFSR),
 * so every fault is a HardFault and the only evidence is the exception frame
 * the core pushed: r0-r3, r12, lr, pc, xpsr. The handler copies it into the
 * record and resets. The next boot prints it as an "E ev=crash" line; the host
 * maps pc/lr to source lines with the ELF for build_crc.
 *
 * Boot loops: a boot counts toward the "streak" only if the previous run
 * ended in a crash or a watchdog reset. After DIAG_SAFE_STREAK such boots in a
 * row the firmware starts in safe mode (sensors off, telemetry and commands
 * on). Running DIAG_STABLE_MS in normal mode clears the streak. */
#ifndef DIAG_H
#define DIAG_H

#include <stdint.h>

#define DIAG_SAFE_STREAK  3u
#define DIAG_STABLE_MS    10000u

typedef enum {   /* checkpoints: where the main loop last was (shown after a watchdog reset) */
    CP_NONE = 0, CP_BOOT, CP_INIT, CP_I2C_INIT, CP_ACC_INIT, CP_LOOP, CP_ACC_READ,
    CP_ACC_CHECK, CP_US, CP_FRAME, CP_HEALTH, CP_CMD, CP_INJECT_HANG, CP__COUNT
} diag_cp_t;

typedef enum { MODE_NORMAL = 0, MODE_SAFE = 1 } diag_mode_t;

typedef struct {
    uint32_t r0, r1, r2, r3, r12, lr, pc, xpsr;
    uint32_t sp;          /* value of SP before the exception frame was pushed */
    uint32_t build_crc;   /* which firmware crashed */
    uint32_t uptime_ms;
    uint32_t checkpoint;
} diag_crash_t;

typedef struct {
    uint32_t reset_flags;     /* RCC->CSR at boot */
    uint32_t boot_count;      /* boots since the record was created (power-on) */
    uint32_t boot_streak;
    uint32_t crash_count;
    uint32_t wdg_count;
    uint32_t last_checkpoint; /* checkpoint at the end of the previous run */
    uint32_t build_crc;       /* CRC-32 of g_fw_info.build_id */
    diag_mode_t mode;
    uint8_t  new_crash;       /* the previous run crashed (crash record is fresh) */
    uint8_t  record_fresh;    /* .noinit record was invalid and got re-created */
    uint8_t  new_build;       /* first boot of this build */
} diag_info_t;

extern diag_info_t g_diag;
extern volatile uint32_t g_inject_flags;   /* persists across resets; see INJ_* in cmd.c */

void        diag_early(void);              /* first thing in main(): reset cause, record, stack paint */
void        diag_tick(uint32_t now_ms);    /* each loop: clears the streak after DIAG_STABLE_MS */
void        diag_cp(diag_cp_t cp);         /* record a checkpoint */
const char *diag_cp_name(uint32_t cp);
void        diag_reset_str(uint32_t flags, char *out, int len);  /* "iwdg,sft" */
int         diag_get_crash(diag_crash_t *out);   /* 1 if a crash record exists */
void        diag_clear_crash(void);
void        diag_set_safe(int on);         /* "safe on" persists across resets; "off" clears the streak */
void        diag_set_inject(uint32_t flags);
uint32_t    diag_stack_free(void);         /* bytes of stack never used since boot */
void        diag_wdg_init(void);           /* ~2 s independent watchdog */
void        diag_wdg_kick(void);
uint32_t    diag_micros(void);             /* microseconds since boot, from SysTick */
uint32_t    crc32(const void *data, uint32_t len);

#endif
