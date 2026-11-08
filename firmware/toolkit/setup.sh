#!/usr/bin/env bash
# Install the firmware toolkit into ./opt next to this script.
#
# Everything lands inside this folder, so no root is needed except for the
# optional udev rule. Downloads are cached in ./downloads and checked against
# the SHA-256 values in versions.env.
#
#   ./setup.sh                       install for this machine
#   ./setup.sh --offline             install from ./downloads only (no network)
#   ./setup.sh --udev                also install USB permission rules (sudo)
#   ./setup.sh --full                keep the whole compiler (by default it's trimmed
#                                    to Cortex-M C/C++: about 1.1 GB down to ~0.4 GB)
#   ./setup.sh --download-only --platform linux-x64
#                                    only fetch tarballs for another host (pack.sh uses this)
set -euo pipefail

TK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
source "$TK/versions.env"
DL="$TK/downloads"
OPT="$TK/opt"

OFFLINE=0
UDEV=0
DOWNLOAD_ONLY=0
SLIM=1
PLATFORM=""

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --udev) UDEV=1 ;;
    --full) SLIM=0 ;;
    --download-only) DOWNLOAD_ONLY=1 ;;
    --platform) PLATFORM="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

detect_platform() {
  local os arch
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os" in Linux) os=linux ;; Darwin) os=darwin ;; *) die "unsupported OS: $os" ;; esac
  case "$arch" in
    aarch64|arm64) arch=arm64 ;;
    x86_64|amd64) arch=x64 ;;
    *) die "unsupported CPU: $arch (xPack builds exist for arm64 and x64)" ;;
  esac
  echo "$os-$arch"
}

sha256_of() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

download() { # url dest
  local quiet=-sS; [[ -t 2 ]] && quiet=--progress-bar   # no progress-bar spam in logs
  if command -v curl >/dev/null; then curl -fL --retry 3 "$quiet" -o "$2" "$1"
  elif command -v wget >/dev/null; then wget -q --show-progress -O "$2" "$1"
  else die "need curl or wget to download"; fi
}

# fetch <url> <file name in downloads/> <expected sha256>
fetch() {
  local url="$1" file="$DL/$2" want="$3" got
  if [[ -f "$file" && -n "$want" ]]; then
    got="$(sha256_of "$file")"
    if [[ "$got" == "$want" ]]; then echo "    cached: $2"; return; fi
    warn "$2 is in the cache but its checksum doesn't match; downloading it again"
    rm -f "$file"
  fi
  [[ $OFFLINE -eq 0 ]] || die "offline mode, and $2 is missing from $DL"
  mkdir -p "$DL"
  echo "    downloading $2"
  download "$url" "$file.part"
  got="$(sha256_of "$file.part")"
  if [[ -z "$want" ]]; then
    rm -f "$file.part"
    die "no SHA-256 pinned for $2 in versions.env.
    The download's SHA-256 is $got
    Check it against the publisher's checksum, then add it to versions.env."
  fi
  if [[ "$got" != "$want" ]]; then
    rm -f "$file.part"
    die "checksum mismatch for $2
    expected $want
    got      $got"
  fi
  mv "$file.part" "$file"
}

# install_tarball <file in downloads/> <link name in opt/> [paths to extract...]
# Extracts into opt/ and points opt/<link> at the extracted top-level folder.
install_tarball() {
  local name="$1" file="$DL/$1" link="$2"; shift 2
  local top tmp
  # '|| true': head exits early, which makes tar fail with SIGPIPE under pipefail
  top="$(tar tzf "$file" | head -1 | cut -d/ -f1 || true)"
  [[ -n "$top" ]] || die "can't read $name"
  if [[ ! -d "$OPT/$top" ]]; then
    echo "    extracting $name"
    # Extract to a temp dir first, so an interrupted run never leaves a half-installed tool.
    tmp="$OPT/.extract-$link"
    rm -rf "$tmp"; mkdir -p "$tmp"
    # GNU tar needs --wildcards for patterns; BSD tar (macOS) matches globs by default.
    if tar --version 2>/dev/null | grep GNU >/dev/null; then
      tar xzf "$file" -C "$tmp" --wildcards "$@"
    else
      tar xzf "$file" -C "$tmp" "$@"
    fi
    mv "$tmp/$top" "$OPT/$top"
    rm -rf "$tmp"
  fi
  ln -sfn "$top" "$OPT/$link"
}

# Trim the compiler to what Cortex-M firmware needs: drop the Cortex-A/R and
# classic-ARM multilibs, Fortran, and the Python-enabled gdb (plain
# arm-none-eabi-gdb stays). The thumb multilibs to keep are set in versions.env.
slim_gcc() {
  local g="$OPT/gcc" d keep
  [[ -f "$g/.slim" ]] && return
  echo "    trimming the compiler to Cortex-M (use --full to keep everything)"
  for d in "$g"/arm-none-eabi/lib/thumb/* "$g"/lib/gcc/arm-none-eabi/*/thumb/*; do
    keep=0
    for m in $GCC_KEEP_MULTILIBS; do [[ "$(basename "$d")" == "$m" ]] && keep=1; done
    [[ $keep -eq 1 ]] || rm -rf "$d"
  done
  rm -rf "$g"/arm-none-eabi/lib/arm "$g"/lib/gcc/arm-none-eabi/*/arm \
         "$g"/bin/*gfortran* "$g"/libexec/gcc/arm-none-eabi/*/f951 \
         "$g"/lib/gcc/arm-none-eabi/*/finclude "$g"/lib/gcc/arm-none-eabi/*/libcaf_single.a \
         "$g"/bin/*-py3 "$g"/lib/python3*
  find "$g/" -name 'libgfortran*' -delete   # trailing / : $g is a symlink
  echo "$GCC_KEEP_MULTILIBS" > "$g/.slim"
}

plat_var() { # GCC linux-arm64 -> value of GCC_SHA256_linux_arm64
  local v="${1}_SHA256_${2//-/_}"
  echo "${!v:-}"
}

[[ -n "$PLATFORM" ]] || PLATFORM="$(detect_platform)"
say "platform: $PLATFORM"

# 1) Arm GNU toolchain
gcc_tgz="xpack-arm-none-eabi-gcc-${GCC_VERSION}-${PLATFORM}.tar.gz"
say "arm-none-eabi-gcc $GCC_VERSION"
fetch "https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack/releases/download/v${GCC_VERSION}/${gcc_tgz}" \
      "$gcc_tgz" "$(plat_var GCC "$PLATFORM")"

# 2) OpenOCD
ocd_tgz="xpack-openocd-${OPENOCD_VERSION}-${PLATFORM}.tar.gz"
say "openocd $OPENOCD_VERSION"
fetch "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/v${OPENOCD_VERSION}/${ocd_tgz}" \
      "$ocd_tgz" "$(plat_var OPENOCD "$PLATFORM")"

# 3) CMSIS core and device headers
core_tgz="CMSIS_6-v${CMSIS_CORE_VERSION}.tar.gz"
say "CMSIS core $CMSIS_CORE_VERSION"
fetch "https://codeload.github.com/ARM-software/CMSIS_6/tar.gz/refs/tags/v${CMSIS_CORE_VERSION}" \
      "$core_tgz" "$CMSIS_CORE_SHA256"

for fam in $CMSIS_DEVICE_FAMILIES; do
  FAM="$(echo "$fam" | tr '[:lower:]' '[:upper:]')"
  ver_var="CMSIS_DEVICE_${FAM}_VERSION"; sha_var="CMSIS_DEVICE_${FAM}_SHA256"
  ver="${!ver_var:-}"; [[ -n "$ver" ]] || die "$ver_var is not set in versions.env"
  say "CMSIS device $fam $ver"
  fetch "https://codeload.github.com/STMicroelectronics/cmsis-device-${fam}/tar.gz/refs/tags/v${ver}" \
        "cmsis-device-${fam}-v${ver}.tar.gz" "${!sha_var:-}"
done

if [[ $DOWNLOAD_ONLY -eq 1 ]]; then say "downloads are in $DL"; exit 0; fi
[[ "$PLATFORM" == "$(detect_platform)" ]] || die "--platform differs from this machine; use it only with --download-only"

say "installing into $OPT"
if [[ $SLIM -eq 0 && -f "$OPT/gcc/.slim" ]]; then
  echo "    --full: removing the trimmed compiler to extract it again"
  rm -rf "$OPT/$(readlink "$OPT/gcc")"
fi
install_tarball "$gcc_tgz" gcc
[[ $SLIM -eq 0 ]] || slim_gcc
install_tarball "$ocd_tgz" openocd
# Keep only the parts of CMSIS we use (the full repos are much larger).
install_tarball "$core_tgz" cmsis-core "CMSIS_6-${CMSIS_CORE_VERSION}/CMSIS/Core/Include" "CMSIS_6-${CMSIS_CORE_VERSION}/LICENSE"
for fam in $CMSIS_DEVICE_FAMILIES; do
  FAM="$(echo "$fam" | tr '[:lower:]' '[:upper:]')"
  ver_var="CMSIS_DEVICE_${FAM}_VERSION"; ver="${!ver_var}"
  install_tarball "cmsis-device-${fam}-v${ver}.tar.gz" "cmsis-device-${fam}" \
    "cmsis-device-${fam}-${ver}/Include" "cmsis-device-${fam}-${ver}/Source/Templates/gcc" \
    "cmsis-device-${fam}-${ver}/Source/Templates/system_stm32${fam}xx.c" "cmsis-device-${fam}-${ver}/LICENSE*"
done

# 4) USB permissions (Linux). Without these, openocd needs sudo.
if [[ "$(uname -s)" == Linux ]]; then
  rules="$OPT/openocd/openocd/contrib/60-openocd.rules"
  have_rules=0
  for f in /etc/udev/rules.d/*openocd* /usr/lib/udev/rules.d/*openocd* /lib/udev/rules.d/*openocd*; do
    [[ -e "$f" ]] && have_rules=1
  done
  if [[ $have_rules -eq 1 ]]; then
    echo "    udev rules for debug probes are already installed"
  elif [[ $UDEV -eq 1 ]]; then
    say "installing udev rules (needs sudo)"
    sudo install -m 0644 "$rules" /etc/udev/rules.d/60-openocd.rules
    sudo udevadm control --reload-rules && sudo udevadm trigger
    echo "    done. Unplug and replug the probe."
  else
    warn "no udev rules for debug probes. Run './setup.sh --udev' or use openocd with sudo."
  fi
  if ! id -nG | grep -wE 'plugdev|dialout' >/dev/null; then
    warn "user $(id -un) isn't in plugdev/dialout; the serial port may need: sudo usermod -aG dialout,plugdev $(id -un)"
  fi
fi

# 5) Host tools the build needs that we don't ship
for t in make python3; do
  command -v "$t" >/dev/null || warn "'$t' isn't installed (Debian/Ubuntu: sudo apt install $t)"
done

set +o pipefail   # head below closes pipes early; that's fine here
say "installed:"
"$OPT/gcc/bin/arm-none-eabi-gcc" --version | head -1 | sed 's/^/    /'
"$OPT/openocd/bin/openocd" --version 2>&1 | head -1 | sed 's/^/    /'
echo
echo "Next: 'source $TK/env.sh' to put the tools on PATH (the Makefiles find them on their own)."
