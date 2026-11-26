#include "alarm_state.h"
#include <cassert>

int main() {
    deskbot_alarm::Daily alarm;
    assert(!alarm.enabled && !alarm.Due(2026,300,8,0));
    assert(alarm.RestoreOrMigrate(0,480,true));
    assert(!alarm.enabled && alarm.hour==8 && alarm.minute==0);
    assert(!alarm.Due(2026,300,8,0));
    assert(!alarm.RestoreOrMigrate(1,480,false));
    assert(!alarm.enabled && alarm.hour==8 && alarm.minute==0);
    alarm.Restore(480,false);assert(!alarm.Due(2026,300,8,0));
    assert(alarm.Set(8,0));assert(!alarm.Due(1970,0,8,0));
    assert(!alarm.Due(2026,300,7,59));assert(alarm.Due(2026,300,8,0));
    assert(!alarm.Due(2026,300,8,0));assert(!alarm.Due(2026,300,9,0));
    assert(!alarm.Due(2026,300,8,0));assert(alarm.Due(2026,301,8,0));
    alarm.Cancel();assert(!alarm.enabled && !alarm.Due(2026,302,8,0));
    assert(alarm.Set(23,59));assert(alarm.Due(2026,302,23,59));
    alarm.Restore(0,true);assert(alarm.hour==0 && alarm.minute==0);
    assert(alarm.Due(2026,303,0,0));
    alarm.Restore(1439,true);assert(alarm.hour==23 && alarm.minute==59);
    alarm.Restore(1440,true);assert(!alarm.enabled);
    alarm.Restore(-1,true);assert(!alarm.enabled);
    assert(!alarm.Set(24,0));assert(!alarm.Set(0,60));assert(!alarm.Set(-1,0));
    return 0;
}
