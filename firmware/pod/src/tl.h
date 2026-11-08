/* Telemetry line builder.
 *
 * Every line is "<TYPE> key=value key=value ...\r\n". Values never contain
 * spaces unless they're quoted (key="a b"). TYPE is one letter:
 *   D  ultrasonic distance sample      (droppable)
 *   A  accelerometer sample            (droppable)
 *   F  compact radio frame, hex        (droppable)
 *   H  health record, once a second
 *   E  event (boot, crash, sensor error, ...)
 *   R  response to a command
 * Lines starting with '#' are human-readable comments.
 *
 * Droppable lines are skipped whole when the TX ring can't hold them, so the
 * host never sees half a line; the skips are counted in tx_drop_lines. The
 * other types block until there's room, because losing them loses evidence. */
#ifndef TL_H
#define TL_H

#include <stdint.h>

void tl_begin(char type);                 /* blocking line */
void tl_begin_drop(char type);            /* droppable line */
void tl_u(const char *k, uint32_t v);
void tl_i(const char *k, int32_t v);
void tl_x(const char *k, uint32_t v, int digits);   /* k=0x0000ABCD */
void tl_s(const char *k, const char *s);  /* bare word */
void tl_q(const char *k, const char *s);  /* quoted string */
void tl_fixed(const char *k, int32_t v, unsigned decimals);
void tl_hex_bytes(const char *k, const uint8_t *p, uint32_t n);   /* k=A1B2... */
void tl_end(void);

/* Response lines carry the request id that the host sent ("@<id> cmd ...").
 * cmd.c sets it while a command runs. */
extern uint32_t g_tl_resp_id;
void tl_resp_begin(void);                 /* "R id=<n>" */

#endif
