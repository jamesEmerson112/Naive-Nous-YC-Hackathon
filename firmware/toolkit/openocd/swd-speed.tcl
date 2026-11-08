# Pin the SWD clock to the SWD_KHZ environment variable (the Makefile exports
# it; default 1000).
#
# Load this after the target config. Many target configs speed the clock up
# after reset (stm32f0x.cfg asks for 8 MHz in reset-init). The ST-LINK-as-J-Link
# probe this toolkit was built on gets SWD parity errors at 4 MHz but is solid at
# 2 MHz. The target's own reset handlers still run; we just restore the clock after them.
if {[info exists ::env(SWD_KHZ)]} { set SWD_KHZ $::env(SWD_KHZ) } else { set SWD_KHZ 1000 }
adapter speed $SWD_KHZ
foreach _t [target names] {
    foreach _ev {reset-start reset-init} {
        set _old ""
        catch { set _old [$_t cget -event $_ev] }
        $_t configure -event $_ev "$_old
adapter speed $::SWD_KHZ"
    }
}
