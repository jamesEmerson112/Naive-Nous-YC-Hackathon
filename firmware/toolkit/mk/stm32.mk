# Shared build, flash and debug rules for STM32 projects (bare metal + CMSIS).
#
# A project Makefile sets the variables below, then does
#   include <path>/toolkit/mk/stm32.mk
#
# Required:
#   PROJECT         output name, e.g. hello
#   CPU             -mcpu value, e.g. cortex-m0, cortex-m4
#   DEVICE          CMSIS device macro, e.g. STM32F042x6
#   FAMILY          CMSIS family, e.g. f0 (setup.sh must have fetched cmsis-device-<family>)
#   STARTUP         startup file name from the CMSIS gcc templates, e.g. startup_stm32f042x6.s
#   LDSCRIPT        linker script
#   OPENOCD_TARGET  OpenOCD target config, e.g. target/stm32f0x.cfg
#   SRCS            your C sources
# Optional:
#   FPU             extra float flags, e.g. -mfpu=fpv4-sp-d16 -mfloat-abi=hard
#   INCLUDES        extra include dirs
#   DEFS            extra -D defines
#   NEWLIB_STUBS    syscall stubs (default toolkit/src/newlib_stubs.c; set empty to drop)
#   OPT             optimisation level (default -Os)
#   PROBE           debug probe config in toolkit/probes/ (default jlink)
#   SWD_KHZ         SWD clock in kHz (default 1000; see openocd/swd-speed.tcl)
#   PORT            serial port for monitor/check (default: auto-detect)
#   BAUD            serial baud rate (default 115200)

TOOLKIT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
OPTDIR  := $(TOOLKIT)/opt

# Use the toolkit's own tools when installed, otherwise whatever is on PATH.
ifneq ($(wildcard $(OPTDIR)/gcc/bin/arm-none-eabi-gcc),)
  PREFIX := $(OPTDIR)/gcc/bin/arm-none-eabi-
else
  PREFIX := arm-none-eabi-
endif
ifneq ($(wildcard $(OPTDIR)/openocd/bin/openocd),)
  OPENOCD := $(OPTDIR)/openocd/bin/openocd
else
  OPENOCD := openocd
endif

CC      := $(PREFIX)gcc
OBJCOPY := $(PREFIX)objcopy
OBJDUMP := $(PREFIX)objdump
SIZE    := $(PREFIX)size
NM      := $(PREFIX)nm
GDB     := $(PREFIX)gdb
PYTHON  ?= python3
FWTOOL  := $(PYTHON) $(TOOLKIT)/bin/fwtool

CMSIS_CORE   := $(OPTDIR)/cmsis-core/CMSIS/Core/Include
CMSIS_DEVICE := $(OPTDIR)/cmsis-device-$(FAMILY)
SYSTEM_SRC   ?= $(CMSIS_DEVICE)/Source/Templates/system_stm32$(FAMILY)xx.c
STARTUP_SRC  ?= $(CMSIS_DEVICE)/Source/Templates/gcc/$(STARTUP)
NEWLIB_STUBS ?= $(TOOLKIT)/src/newlib_stubs.c

PROBE ?= jlink
SWD_KHZ ?= 1000
PORT  ?= auto
BAUD  ?= 115200
OPT   ?= -Os
BUILD ?= build

export SWD_KHZ   # read by openocd/swd-speed.tcl
OPENOCD_ARGS := -f $(TOOLKIT)/probes/$(PROBE).cfg -f $(OPENOCD_TARGET) -f $(TOOLKIT)/openocd/swd-speed.tcl
OCD := $(OPENOCD) $(OPENOCD_ARGS)

# Build ID: git revision + build time. Baked into the firmware (see the
# .fw_info section) so `make check` can confirm the chip runs this exact build.
# Regenerated only when a source file changes.
GIT_REV := $(shell git -C $(CURDIR) describe --always --dirty --abbrev=7 2>/dev/null || echo nogit)
BUILD_ID_H := $(BUILD)/fw_build_id.h

ARCH    := -mcpu=$(CPU) -mthumb $(FPU)
CFLAGS  := $(ARCH) $(OPT) -g3 -std=c11 -Wall -Wextra -Wshadow -Wundef \
           -ffunction-sections -fdata-sections -fno-common \
           -D$(DEVICE) $(DEFS) \
           -I$(BUILD) -I$(CMSIS_CORE) -I$(CMSIS_DEVICE)/Include $(addprefix -I,$(INCLUDES)) \
           -MMD -MP
ASFLAGS := $(ARCH) -g3 -x assembler-with-cpp
LDFLAGS := $(ARCH) -T$(LDSCRIPT) --specs=nano.specs --specs=nosys.specs \
           -Wl,--gc-sections -Wl,-Map=$(BUILD)/$(PROJECT).map -Wl,--print-memory-usage \
           -Wl,--no-warn-rwx-segments

ALL_SRCS := $(SRCS) $(SYSTEM_SRC) $(NEWLIB_STUBS)
# Objects go under build/, mirroring each source's path (toolkit files under build/toolkit/).
obj_of = $(BUILD)/$(patsubst $(TOOLKIT)/%,toolkit/%,$(patsubst $(CURDIR)/%,%,$(abspath $(basename $(1))))).o
OBJS := $(foreach s,$(ALL_SRCS),$(call obj_of,$(s))) $(call obj_of,$(STARTUP_SRC))
ELF  := $(BUILD)/$(PROJECT).elf

.PHONY: all flash check monitor reset erase gdb gdbserver backup restore size disasm clean toolkit-check help
.DEFAULT_GOAL := all

all: toolkit-check $(ELF) $(BUILD)/$(PROJECT).hex $(BUILD)/$(PROJECT).bin

toolkit-check:
	@test -x "$(CC)" || command -v "$(CC)" >/dev/null || { echo "arm-none-eabi-gcc not found: run $(TOOLKIT)/setup.sh"; exit 1; }
	@test -f "$(CMSIS_DEVICE)/Include/stm32$(FAMILY)xx.h" || { echo "CMSIS for '$(FAMILY)' missing: add it to toolkit/versions.env and rerun setup.sh"; exit 1; }

$(BUILD_ID_H): $(SRCS) $(wildcard $(addsuffix /*.h,$(INCLUDES))) $(LDSCRIPT) $(MAKEFILE_LIST)
	@mkdir -p $(dir $@)
	@echo '#define FW_BUILD_ID "$(PROJECT) $(GIT_REV) $(shell date -u +%Y-%m-%dT%H:%M:%SZ)"' > $@
	@echo "  GEN     $@: $$(cut -d'"' -f2 $@)"

# Every C object waits for the build ID header (cheap, and covers first builds).
$(BUILD)/%.o: %.c | $(BUILD_ID_H)
	@mkdir -p $(dir $@)
	@echo "  CC      $<"
	@$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/toolkit/%.o: $(TOOLKIT)/%.c
	@mkdir -p $(dir $@)
	@echo "  CC      $(notdir $<)"
	@$(CC) $(CFLAGS) -w -c $< -o $@

$(BUILD)/toolkit/%.o: $(TOOLKIT)/%.s
	@mkdir -p $(dir $@)
	@echo "  AS      $(notdir $<)"
	@$(CC) $(ASFLAGS) -c $< -o $@

$(BUILD)/%.o: %.s
	@mkdir -p $(dir $@)
	@echo "  AS      $<"
	@$(CC) $(ASFLAGS) -c $< -o $@

$(ELF): $(OBJS) $(LDSCRIPT)
	@echo "  LD      $@"
	@$(CC) $(LDFLAGS) $(OBJS) -o $@
	@$(SIZE) $@

$(BUILD)/%.hex: $(BUILD)/%.elf
	@$(OBJCOPY) -O ihex $< $@

$(BUILD)/%.bin: $(BUILD)/%.elf
	@$(OBJCOPY) -O binary $< $@

size: $(ELF)
	@$(SIZE) -A -d $(ELF) | grep -vE '^\.(debug|comment|ARM.attr)'

disasm: $(ELF)
	@$(OBJDUMP) -d -S $(ELF) > $(BUILD)/$(PROJECT).lst && echo "wrote $(BUILD)/$(PROJECT).lst"

# Write the firmware, verify it, and start it.
flash: all
	$(OCD) -c "program $(ELF) verify reset exit"

# Confirm the chip is running this build: compare the build ID in the chip's
# flash with build/<project>.elf, check the uptime counter is advancing, and
# look for the boot banner on the serial port.
check:
	@test -f $(ELF) || { echo "no $(ELF): run 'make flash' first"; exit 1; }
	@$(FWTOOL) check --elf $(ELF) --nm $(NM) --objcopy $(OBJCOPY) --openocd "$(OCD)" --port $(PORT) --baud $(BAUD)

monitor:
	@$(FWTOOL) monitor --port $(PORT) --baud $(BAUD)

reset:
	$(OCD) -c "init; reset run; shutdown"

erase:
	$(OCD) -c "init; reset halt; flash erase_sector 0 0 last; shutdown"

# Save the chip's whole flash to backups/ (do this before overwriting firmware you didn't build).
backup:
	@mkdir -p backups
	$(OCD) -c "init; reset halt; flash read_bank 0 backups/flash-$$(date +%Y%m%d-%H%M%S).bin; reset run; shutdown"
	@ls -l backups | tail -1

# Write a raw flash image (from `make backup`, say) back to the chip: make restore BIN=backups/flash-....bin
restore:
	@test -n "$(BIN)" -a -f "$(BIN)" || { echo "usage: make restore BIN=<file.bin>"; exit 1; }
	$(OCD) -c "program $(abspath $(BIN)) 0x08000000 verify reset exit"

# Debug in gdb; OpenOCD runs as a pipe, so nothing else needs starting.
gdb: $(ELF)
	$(GDB) -q $(ELF) \
	  -ex 'target extended-remote | $(OCD) -c "gdb_port pipe; log_output $(BUILD)/openocd.log"' \
	  -ex 'monitor reset halt'

# A standalone GDB server on :3333 (for VS Code Cortex-Debug or a remote gdb).
gdbserver:
	$(OCD) -c "bindto 0.0.0.0"

clean:
	rm -rf $(BUILD)

help:
	@echo "make            build $(ELF)"
	@echo "make flash      build, write, verify and run on the chip (probe: $(PROBE))"
	@echo "make check      confirm the chip runs this exact build"
	@echo "make monitor    serial console (exit with Ctrl-])"
	@echo "make gdb        debug in gdb; 'make gdbserver' for an IDE"
	@echo "make backup     save the chip's flash; 'make restore BIN=file.bin' writes one back"
	@echo "make reset | erase | size | disasm | clean"

-include $(OBJS:.o=.d)
