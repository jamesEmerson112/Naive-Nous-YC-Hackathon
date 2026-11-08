#include "diag.h"
#include "fw_info.h"
#include "stm32f0xx.h"

#define NOINIT_MAGIC  0x4E4F494Eu   /* "NOIN" */
#define STACK_PAINT   0xA5A5A5A5u

typedef struct {
    uint32_t magic;
    uint32_t boot_count;
    uint32_t boot_streak;
    uint32_t last_build_crc;
    uint32_t crash_valid;      /* a crash record is stored */
    uint32_t crash_fresh;      /* set by the fault handler, cleared at the next boot */
    uint32_t crash_count;
    uint32_t wdg_count;
    uint32_t force_safe;
    uint32_t inject_flags;
    diag_crash_t crash;
    uint32_t check;            /* checksum of everything above */
} noinit_t;

__attribute__((section(".noinit"))) static noinit_t nr;
/* The checkpoint changes several times per loop, so it lives outside the
 * checksummed record and is validated by keeping its complement alongside. */
__attribute__((section(".noinit"))) static volatile uint32_t cp_now, cp_inv;

diag_info_t g_diag;
volatile uint32_t g_inject_flags;

extern uint32_t end;           /* linker: end of .bss/.noinit, bottom of the stack area */
extern uint32_t _estack;

static uint32_t nr_sum(void)
{
    const uint32_t *p = (const uint32_t *)&nr;
    uint32_t s = 0x5EEDu;
    for (uint32_t i = 0; i < (sizeof(nr) / 4u) - 1u; i++) s = (s ^ p[i]) * 16777619u + i;
    return s;
}

static void nr_seal(void)
{
    nr.check = nr_sum();
}

uint32_t crc32(const void *data, uint32_t len)   /* IEEE 802.3, same as Python zlib.crc32 */
{
    const uint8_t *p = data;
    uint32_t c = 0xFFFFFFFFu;
    while (len--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
    }
    return ~c;
}

static uint32_t str_len(const char *s)
{
    uint32_t n = 0;
    while (s[n]) n++;
    return n;
}

static void stack_paint(void)
{
    uint32_t *p = &end;
    uint32_t *top = (uint32_t *)(__get_MSP() - 64u);   /* stay clear of our own frame */
    while (p < top) *p++ = STACK_PAINT;
}

uint32_t diag_stack_free(void)
{
    const uint32_t *p = &end;
    while (p < &_estack && *p == STACK_PAINT) p++;
    return (uint32_t)((uintptr_t)p - (uintptr_t)&end);
}

void diag_early(void)
{
    g_diag.reset_flags = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF;
    g_diag.build_crc = crc32(g_fw_info.build_id, str_len(g_fw_info.build_id));

    if (nr.magic != NOINIT_MAGIC || nr.check != nr_sum()) {
        uint32_t *p = (uint32_t *)&nr;
        for (uint32_t i = 0; i < sizeof(nr) / 4u; i++) p[i] = 0;
        nr.magic = NOINIT_MAGIC;
        g_diag.record_fresh = 1;
    }

    nr.boot_count++;
    if (nr.last_build_crc != g_diag.build_crc) {   /* new firmware: start clean */
        nr.last_build_crc = g_diag.build_crc;
        nr.boot_streak = 0;
        nr.force_safe = 0;
        g_diag.new_build = 1;
    }

    int wdg = (g_diag.reset_flags & RCC_CSR_IWDGRSTF) != 0;
    g_diag.new_crash = (uint8_t)nr.crash_fresh;
    if (wdg) nr.wdg_count++;
    if (wdg || nr.crash_fresh) nr.boot_streak++;
    else nr.boot_streak = 0;
    nr.crash_fresh = 0;

    g_diag.last_checkpoint = (cp_now == ~cp_inv && !g_diag.record_fresh) ? cp_now : CP_NONE;
    diag_cp(CP_BOOT);
    g_diag.boot_count  = nr.boot_count;
    g_diag.boot_streak = nr.boot_streak;
    g_diag.crash_count = nr.crash_count;
    g_diag.wdg_count   = nr.wdg_count;
    g_diag.mode = (nr.boot_streak >= DIAG_SAFE_STREAK || nr.force_safe) ? MODE_SAFE : MODE_NORMAL;
    g_inject_flags = nr.inject_flags;
    nr_seal();

    stack_paint();
}

void diag_tick(uint32_t now_ms)
{
    if (nr.boot_streak && g_diag.mode == MODE_NORMAL && now_ms >= DIAG_STABLE_MS) {
        nr.boot_streak = 0;
        g_diag.boot_streak = 0;
        nr_seal();
    }
}

void diag_cp(diag_cp_t cp)
{
    cp_now = cp;
    cp_inv = ~(uint32_t)cp;
}

const char *diag_cp_name(uint32_t cp)
{
    static const char *const names[CP__COUNT] = {
        "none", "boot", "init", "i2c_init", "acc_init", "loop", "acc_read",
        "acc_check", "us", "frame", "health", "cmd", "inject_hang",
    };
    return cp < CP__COUNT ? names[cp] : "?";
}

void diag_reset_str(uint32_t f, char *out, int len)
{
    static const struct { uint32_t bit; const char *name; } map[] = {
        { RCC_CSR_PORRSTF, "por" }, { RCC_CSR_PINRSTF, "pin" }, { RCC_CSR_SFTRSTF, "sft" },
        { RCC_CSR_IWDGRSTF, "iwdg" }, { RCC_CSR_WWDGRSTF, "wwdg" }, { RCC_CSR_LPWRRSTF, "lpwr" },
        { RCC_CSR_OBLRSTF, "obl" },
    };
    int n = 0;
    for (unsigned i = 0; i < sizeof(map) / sizeof(map[0]); i++) {
        if (!(f & map[i].bit)) continue;
        if (n && n < len - 1) out[n++] = ',';
        for (const char *s = map[i].name; *s && n < len - 1; s++) out[n++] = *s;
    }
    if (!n && len > 4) { out[n++] = 'n'; out[n++] = 'o'; out[n++] = 'n'; out[n++] = 'e'; }
    out[n] = 0;
}

int diag_get_crash(diag_crash_t *out)
{
    if (!nr.crash_valid) return 0;
    *out = nr.crash;
    return 1;
}

void diag_clear_crash(void)
{
    nr.crash_valid = 0;
    nr_seal();
}

void diag_set_safe(int on)
{
    nr.force_safe = on ? 1u : 0u;
    if (!on) {
        nr.boot_streak = 0;
        g_diag.boot_streak = 0;
    }
    nr_seal();
}

void diag_set_inject(uint32_t flags)
{
    nr.inject_flags = flags;
    g_inject_flags = flags;
    nr_seal();
}

/* ---- HardFault capture --------------------------------------------------- */

extern volatile uint32_t g_uptime_ms;

__attribute__((used)) void diag_hardfault_c(uint32_t *frame)
{
    nr.crash.r0   = frame[0];
    nr.crash.r1   = frame[1];
    nr.crash.r2   = frame[2];
    nr.crash.r3   = frame[3];
    nr.crash.r12  = frame[4];
    nr.crash.lr   = frame[5];
    nr.crash.pc   = frame[6];
    nr.crash.xpsr = frame[7];
    nr.crash.sp   = (uint32_t)(uintptr_t)(frame + 8);
    nr.crash.build_crc  = g_diag.build_crc;
    nr.crash.uptime_ms  = g_uptime_ms;
    nr.crash.checkpoint = cp_now;
    nr.crash_valid = 1;
    nr.crash_fresh = 1;
    nr.crash_count++;
    nr_seal();
    NVIC_SystemReset();
}

/* Find which stack the frame went on (EXC_RETURN bit 2), then hand it to C. */
__attribute__((naked)) void HardFault_Handler(void)
{
    __asm volatile(
        "movs r0, #4          \n"
        "mov  r1, lr          \n"
        "tst  r0, r1          \n"
        "beq  1f              \n"
        "mrs  r0, psp         \n"
        "b    2f              \n"
        "1:                   \n"
        "mrs  r0, msp         \n"
        "2:                   \n"
        "bl   diag_hardfault_c\n");
}

/* ---- watchdog and timing ------------------------------------------------- */

void diag_wdg_init(void)
{
    /* Stop the watchdog while a debugger halts the core, so SWD tools can
     * pause the CPU without causing a reset. */
    RCC->APB2ENR |= RCC_APB2ENR_DBGMCUEN;
    DBGMCU->APB1FZ |= DBGMCU_APB1_FZ_DBG_IWDG_STOP;

    IWDG->KR  = 0xCCCCu;            /* start (LSI ~40 kHz, 30-50 kHz over temperature) */
    IWDG->KR  = 0x5555u;            /* unlock PR/RLR */
    IWDG->PR  = 3u;                 /* /32 -> ~1.25 kHz */
    IWDG->RLR = 2500u;              /* ~2 s (1.6-2.7 s) */
    while (IWDG->SR) {}
    IWDG->KR  = 0xAAAAu;
}

void diag_wdg_kick(void)
{
    IWDG->KR = 0xAAAAu;
}

uint32_t diag_micros(void)
{
    uint32_t ms, val;
    do {
        ms  = g_uptime_ms;
        val = SysTick->VAL;
    } while (ms != g_uptime_ms);
    uint32_t load = SysTick->LOAD + 1u;
    return ms * 1000u + ((load - 1u - val) * 1000u) / load;
}
