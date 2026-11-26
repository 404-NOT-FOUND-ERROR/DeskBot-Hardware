#pragma once
#include <cstdint>

namespace deskbot_alarm {
// The display can suggest 08:00 without arming a daily reminder. Callers
// serialize this small state with the LVGL adapter lock.
class Daily {
public:
    int hour=8, minute=0;
    bool enabled=false;
    bool RestoreOrMigrate(int schema, int minutes, bool armed) {
        // Schema 0 represents the legacy implicit 08:00 alarm; retire it once.
        if (schema < 1) { Restore(480,false); return true; }
        Restore(minutes,armed);return false;
    }
    void Restore(int minutes, bool armed) {
        if (minutes<0 || minutes>=1440) { hour=8;minute=0;enabled=false; }
        else { hour=minutes/60;minute=minutes%60;enabled=armed; }
        last_day_=-1;
    }
    bool Set(int h,int m) {
        if (h<0 || h>23 || m<0 || m>59) return false;
        hour=h;minute=m;enabled=true;last_day_=-1;return true;
    }
    void Cancel() { enabled=false;last_day_=-1; }
    bool Due(int year,int yday,int h,int m) {
        // Ignore unsynchronized clocks and never ring twice on one local day.
        if (!enabled || year<2024 || yday<0 || yday>365 || h!=hour || m!=minute) return false;
        const int day=year*366+yday;
        if (day==last_day_) return false;
        last_day_=day;return true;
    }
private:
    int last_day_=-1;
};
}
