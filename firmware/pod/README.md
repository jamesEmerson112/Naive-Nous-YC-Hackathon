# pod: sensor pod firmware (STM32F042K6 + HC-SR04 + LIS3DH)

This is the reference ("v1.0, known-good") firmware. The healer workspace and the demo images are built
from it.

```bash
make flash      # build, program, verify (any change here: rerun demo/scenario setup + build-images)
```

## Wiring
| part | pin | Nucleo-32 header |
|---|---|---|
| HC-SR04 VCC / GND | 5V / GND | |
| HC-SR04 TRIG | PA0 | A0 |
| HC-SR04 ECHO | PB0 via 10k (series) / 18k (to GND) divider | D3 |
| LIS3DH VIN / GND | 3V3 / GND | |
| LIS3DH SCL | PB6 (directly, or via A5/PA6 and solder bridge SB16) | D5 or A5 |
| LIS3DH SDA | PB7 (directly, or via A4/PA5 and solder bridge SB18) | D4 or A4 |
| LIS3DH CS / SDO | 3V3 (I2C mode) / GND (address 0x18) | |

PA5/PA6 are tied to the I2C lines by the solder bridges, so the firmware never drives them. This wiring was
checked on the board on Nov 7: `WHO_AM_I` read 0x33 at 0x18.

## Modules
| file | does |
|---|---|
| `main.c` | clock (48 MHz), scheduler, telemetry, accelerometer config readback, health record, boot report |
| `diag.c` | reset cause, `.noinit` record, HardFault capture, IWDG + checkpoints, boot-loop → safe mode, stack high-water mark |
| `cmd.c` | line commands (`@<id> cmd` → `R id=<id> ...`), fault injection (`FAULT_INJECT=1`) |
| `uart.c` / `tl.c` | interrupt-driven UART rings; tagged `TYPE key=value` lines, droppable data lines |
| `i2c.c` / `lis3dh.c` | I2C1 with timeouts and bus recovery; LIS3DH driver |
| `hcsr04.c` / `adc.c` | ultrasonic via TIM3 input capture; VDDA, die temperature, ECHO voltage self-test |
| `frame.c` | 16-byte once-a-second radio frame for the XIAO ESP32 relay |

Telemetry format, commands and diagnostics are described in [../healer/SYSTEM.md](../healer/SYSTEM.md).

## Measured on the board
- **Main loop:** worst case ~3.3 ms (a second boundary with accel read + config readback + health), ~1.4 ms otherwise.
- **Stack:** ~4.2 KB never used. **Flash:** 14.5 KB of 32 KB. **RAM:** 2.5 KB static.
- **Accelerometer at rest:** |a| ≈ 1075 mg, noise ~3–6 mg RMS per axis.
- **Fixed relative to the original HC-SR04 project:** the ECHO self-test polled in 1 ms steps and missed
  short echoes from objects under ~17 cm. It now polls in 10 µs steps. `stm32f042k6-hcsr04` still has the
  old code.
