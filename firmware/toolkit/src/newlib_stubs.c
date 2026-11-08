/* Minimal newlib system calls for bare-metal firmware.
 *
 * Linked into every project by mk/stm32.mk. They replace libnosys's versions,
 * which make the linker warn "_write is not implemented and will always fail".
 * All are weak: to send printf() to a UART, define _write() in your project:
 *
 *   int _write(int fd, const char *buf, int len) {
 *       for (int i = 0; i < len; i++) uart_putc(buf[i]);
 *       return len;
 *   }
 *
 * (_sbrk, used by malloc, still comes from libnosys; size the heap with
 * _Min_Heap_Size in the linker script.) */
#include <errno.h>
#include <sys/stat.h>

#define WEAK __attribute__((weak))

WEAK int _write(int fd, const char *buf, int len) { (void)fd; (void)buf; return len; }
WEAK int _read(int fd, char *buf, int len)        { (void)fd; (void)buf; (void)len; errno = EAGAIN; return -1; }
WEAK int _close(int fd)                           { (void)fd; errno = EBADF; return -1; }
WEAK int _lseek(int fd, int off, int whence)      { (void)fd; (void)off; (void)whence; return 0; }
WEAK int _fstat(int fd, struct stat *st)          { (void)fd; st->st_mode = S_IFCHR; return 0; }
WEAK int _isatty(int fd)                          { (void)fd; return 1; }
WEAK int _getpid(void)                            { return 1; }
WEAK int _kill(int pid, int sig)                  { (void)pid; (void)sig; errno = EINVAL; return -1; }
WEAK void _exit(int status)                       { (void)status; for (;;) {} }
