# Source this file to put the toolkit on PATH:   source firmware/toolkit/env.sh
# Works in bash and zsh. The project Makefiles don't need it; it's here for
# running arm-none-eabi-gcc, openocd, arm-none-eabi-gdb or fwtool by hand.
if [ -n "${BASH_SOURCE:-}" ]; then _fw_tk_src="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then _fw_tk_src="${(%):-%x}"
else _fw_tk_src="$0"; fi
FW_TOOLKIT="$(cd "$(dirname "$_fw_tk_src")" && pwd)"
export FW_TOOLKIT
export PATH="$FW_TOOLKIT/opt/gcc/bin:$FW_TOOLKIT/opt/openocd/bin:$FW_TOOLKIT/bin:$PATH"
unset _fw_tk_src
if [ ! -x "$FW_TOOLKIT/opt/gcc/bin/arm-none-eabi-gcc" ]; then
  echo "firmware toolkit isn't installed yet; run $FW_TOOLKIT/setup.sh" >&2
fi
