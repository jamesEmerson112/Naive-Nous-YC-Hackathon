# STM32F042K6 + HC-SR04 ultrasonic distance sensor, streamed as CSV over the
# probe's serial port. Wiring: TRIG -> A0 (PA0), ECHO -> 10k/18k divider -> D3 (PB0).
# Run `make help` for targets. Build rules live in ../toolkit/mk/stm32.mk.

PROJECT        := hcsr04
CPU            := cortex-m0
DEVICE         := STM32F042x6
FAMILY         := f0
STARTUP        := startup_stm32f042x6.s
LDSCRIPT       := stm32f042k6.ld
OPENOCD_TARGET := target/stm32f0x.cfg
SRCS           := src/main.c src/uart.c src/hcsr04.c
INCLUDES       := src

include ../toolkit/mk/stm32.mk
