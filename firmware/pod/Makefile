# Sensor pod firmware for the STM32F042K6 (Nucleo-32): HC-SR04 ultrasonic +
# LIS3DH accelerometer, self-diagnostics, and a line-based command interface.
# Wiring and telemetry format: see README.md. Build rules: toolkit/mk/stm32.mk.
#
# TOOLKIT_DIR lets a copy of this project (the healer workspace) build from
# anywhere: make TOOLKIT_DIR=/abs/path/to/firmware/toolkit

PROJECT        := pod
CPU            := cortex-m0
DEVICE         := STM32F042x6
FAMILY         := f0
STARTUP        := startup_stm32f042x6.s
LDSCRIPT       := stm32f042k6.ld
OPENOCD_TARGET := target/stm32f0x.cfg
SRCS           := src/main.c src/uart.c src/tl.c src/diag.c src/adc.c src/hcsr04.c \
                  src/i2c.c src/lis3dh.c src/frame.c src/cmd.c
INCLUDES       := src

# 1 = include the `inject <fault>` demo commands
FAULT_INJECT   ?= 1
DEFS           += -DFAULT_INJECT=$(FAULT_INJECT)

TOOLKIT_DIR    ?= ../toolkit
include $(TOOLKIT_DIR)/mk/stm32.mk
