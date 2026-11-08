/* Linker script for the pod firmware on the STM32F042K6: 32 KB flash at 0x08000000, 6 KB SRAM at 0x20000000.
 * Symbols _sidata, _sdata, _edata, _sbss, _ebss and _estack are used by the
 * CMSIS startup file (startup_stm32f042x6.s). */

ENTRY(Reset_Handler)

/* The top 128 bytes of RAM hold the .noinit record (boot counters, crash
 * record, watchdog checkpoint, fault-injection flags). Keeping it at the top
 * means it survives not only resets but also reflashing: OpenOCD's flash
 * loader uses the bottom 4 KB of RAM as scratch (stm32f0x.cfg work area
 * 0x20000000-0x20000FFF). A power cycle still clears it. */
MEMORY
{
  FLASH  (rx)  : ORIGIN = 0x08000000, LENGTH = 32K
  RAM    (xrw) : ORIGIN = 0x20000000, LENGTH = 6K - 128
  NOINIT (rw)  : ORIGIN = 0x20001780, LENGTH = 128
}

_estack = ORIGIN(RAM) + LENGTH(RAM);   /* stack grows down from just below .noinit */
_Min_Heap_Size  = 0x000;               /* malloc() gets this much (raise it if you use malloc) */
_Min_Stack_Size = 0x400;               /* link fails if RAM can't fit this much stack */

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } >FLASH

  /* Build ID record read back by `make check` (see src/fw_info.h). */
  .fw_info :
  {
    . = ALIGN(4);
    KEEP(*(.fw_info))
    . = ALIGN(4);
  } >FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text) *(.text*)
    *(.glue_7) *(.glue_7t)
    *(.eh_frame)
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
    _etext = .;
  } >FLASH

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata) *(.rodata*)
    . = ALIGN(4);
  } >FLASH

  .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } >FLASH
  .ARM :
  {
    __exidx_start = .;
    *(.ARM.exidx*)
    __exidx_end = .;
  } >FLASH

  .preinit_array :
  {
    PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*))
    PROVIDE_HIDDEN(__preinit_array_end = .);
  } >FLASH
  .init_array :
  {
    PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*)))
    KEEP(*(.init_array*))
    PROVIDE_HIDDEN(__init_array_end = .);
  } >FLASH
  .fini_array :
  {
    PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*)))
    KEEP(*(.fini_array*))
    PROVIDE_HIDDEN(__fini_array_end = .);
  } >FLASH

  _sidata = LOADADDR(.data);

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data) *(.data*)
    . = ALIGN(4);
    _edata = .;
  } >RAM AT> FLASH

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    __bss_start__ = _sbss;
    *(.bss) *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
    __bss_end__ = _ebss;
  } >RAM

  /* Survives a reset and a reflash (not a power cycle). The startup code
   * neither zeroes nor copies it; diag.c validates it with a magic number
   * and a checksum. */
  .noinit (NOLOAD) :
  {
    . = ALIGN(4);
    *(.noinit) *(.noinit*)
    . = ALIGN(4);
  } >NOINIT

  ._user_heap_stack (NOLOAD) :
  {
    . = ALIGN(8);
    PROVIDE(end = .);
    PROVIDE(_end = .);
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
  } >RAM

  .ARM.attributes 0 : { *(.ARM.attributes) }
}
