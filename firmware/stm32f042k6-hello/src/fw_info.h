/* Build ID record that `make check` (toolkit/bin/fwtool) reads back from the
 * chip over SWD and compares with the freshly built ELF.
 *
 * The linker script keeps it in its own .fw_info section in flash. Keep the
 * layout in sync with FW_INFO_MAGIC / FW_INFO_SIZE in fwtool. */
#ifndef FW_INFO_H
#define FW_INFO_H

#include <stdint.h>
#include "fw_build_id.h"   /* generated in build/ by the Makefile: #define FW_BUILD_ID "..." */

#define FW_INFO_MAGIC 0x31495746u   /* "FWI1" */

typedef struct {
    uint32_t magic;
    char     build_id[60];          /* NUL-terminated */
} fw_info_t;

_Static_assert(sizeof(fw_info_t) == 64, "fwtool expects a 64-byte fw_info record");
_Static_assert(sizeof(FW_BUILD_ID) <= 60, "FW_BUILD_ID is too long for fw_info_t.build_id");

/* Define these once, in main.c: */
extern const fw_info_t g_fw_info;
extern volatile uint32_t g_uptime_ms;   /* milliseconds since boot; check reads it twice */

#endif
