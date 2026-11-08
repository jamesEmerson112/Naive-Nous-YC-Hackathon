# STM32F042K6 (Cortex-M0, 32 KB flash, 6 KB RAM) sanity firmware.
# Run `make help` for targets. Build rules live in ../toolkit/mk/stm32.mk.

PROJECT        := hello
CPU            := cortex-m0
DEVICE         := STM32F042x6
FAMILY         := f0
STARTUP        := startup_stm32f042x6.s
LDSCRIPT       := stm32f042k6.ld
OPENOCD_TARGET := target/stm32f0x.cfg
SRCS           := src/main.c
INCLUDES       := src

include ../toolkit/mk/stm32.mk
