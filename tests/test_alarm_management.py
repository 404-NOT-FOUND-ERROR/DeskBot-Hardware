import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
BOARD=ROOT/'firmware/body/main/boards/esp_vocat'

def function(source, signature):
    start=source.index(signature)
    brace=source.index('{',start)
    depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]


class AlarmTests(unittest.TestCase):
    def compile_and_run(self, source, name):
        spec=importlib.util.spec_from_file_location('compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler();folder=ROOT/'tmp/alarm-management-cpp';folder.mkdir(exist_ok=True)
        cc=folder/(name+'.cc');exe=folder/(name+'.exe');cc.write_text(source,encoding='utf-8')
        result=subprocess.run(command+(['c++'] if kind=='zig-c++' else [])+['-std=c++17','-UNDEBUG',
            '-I',str(BOARD/'customer_ui'),str(cc),'-o',str(exe)],capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(subprocess.run([str(exe)],timeout=10).returncode,0)

    def test_production_timer_and_voice_controls_work_away_from_timer_page(self):
        source=(BOARD/'customer_ui/alarm_pomodoro.cc').read_text('utf-8')
        manager=(BOARD/'customer_ui/alarm_manager.cc').read_text('utf-8')
        harness=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#define ESP_LOGI(...) ((void)0)
#define PAGE_POMODORO "POMODORO"
#define PAGE_TIME_UP "TIME_UP"
#define TIMER_MAX_SECONDS 3600
enum {TIMER_STATE_RUNNING,TIMER_STATE_END};
struct lv_timer_t {void* data;bool paused=false;};
struct alarm_pomodoro_ui_t {int32_t remaining_seconds=0;bool was_running_before_zero=false;
    void* time_min_label=nullptr;void* time_sec_label=nullptr;void* progress_arc=nullptr;lv_timer_t* timer=nullptr;};
alarm_pomodoro_ui_t s_pomodoro_ui;
int rings=0,draws=0,locks=0;const char* page="HOME";
void* lv_timer_get_user_data(lv_timer_t* t){return t->data;}
bool lv_timer_get_paused(lv_timer_t* t){return t->paused;}
void lv_timer_pause(lv_timer_t* t){t->paused=true;}
void esp_lv_adapter_lock(int){++locks;}
void esp_lv_adapter_unlock(){assert(locks>0);--locks;}
const char* ui_bridge_get_current_page(){return page;}
void pomodoro_set_state(alarm_pomodoro_ui_t*,int){}
void lv_label_set_text_fmt(void*,const char*,int){++draws;}
void lv_label_set_text(void*,const char*){++draws;}
void lv_arc_set_angles(void*,int,int){++draws;}
void update_knob_positions(alarm_pomodoro_ui_t*){++draws;}
void alarm_time_up_set_origin(const char*){++rings;}
void main_ui_switch_page(const char* p){page=p;}
void alarm_pomodoro_start(){if(s_pomodoro_ui.timer)s_pomodoro_ui.timer->paused=false;}
void alarm_pomodoro_pause(){if(s_pomodoro_ui.timer)s_pomodoro_ui.timer->paused=true;}
'''
        for signature in ('static void timer_tick_cb(', 'bool alarm_pomodoro_get_status('):
            harness+=function(source,signature)+'\n'
        for signature in ('bool alarm_resume_pomodoro(', 'bool alarm_pause_pomodoro('):
            harness+=function(manager,signature)+'\n'
        harness+=r'''
int main(){
    lv_timer_t timer{&s_pomodoro_ui};s_pomodoro_ui.timer=&timer;
    s_pomodoro_ui.remaining_seconds=2;
    assert(alarm_pause_pomodoro()&&timer.paused&&!strcmp(page,"HOME"));
    assert(alarm_resume_pomodoro()&&!timer.paused&&!strcmp(page,"HOME"));
    timer_tick_cb(&timer);assert(s_pomodoro_ui.remaining_seconds==1&&draws==0&&rings==0);
    int32_t seconds=0;bool running=false;
    assert(alarm_pomodoro_get_status(&seconds,&running)&&seconds==1&&running);
    timer_tick_cb(&timer);timer_tick_cb(&timer);
    assert(s_pomodoro_ui.remaining_seconds==0&&timer.paused&&rings==1&&!strcmp(page,PAGE_TIME_UP));
    timer_tick_cb(&timer);assert(rings==1); // no duplicate reminder
    page="HOME";s_pomodoro_ui.was_running_before_zero=false;timer.paused=false;
    timer_tick_cb(&timer);assert(rings==1&&!strcmp(page,"HOME")); // cancel/reset does not ring
    assert(locks==0);
}
'''
        self.compile_and_run(harness,'timer-integration')

    def test_disabled_daily_alarm_still_refreshes_visible_sleep_page(self):
        source=(BOARD/'customer_ui/alarm_sleep_24h.cc').read_text('utf-8')
        harness=r'''
#include "alarm_state.h"
#include <cassert>
#include <cstdint>
#include <ctime>
#ifdef _WIN32
static tm* localtime_r(const time_t* now,tm* out){*out=*std::localtime(now);return out;}
#endif
#define ESP_LOGI(...) ((void)0)
#define PAGE_SLEEP "SLEEP"
#define PAGE_TIME_UP "TIME_UP"
#define LV_OBJ_FLAG_HIDDEN 1
struct alarm_sleep_24h_ui_t {void* container;bool has_jumped_to_time_up=false;int start_angle=0,end_angle=120;};
struct lv_timer_t {void* data;};
deskbot_alarm::Daily s_daily;int32_t s_sleep_end_hour=8,s_sleep_end_min=0;
int redraws=0,rings=0;bool hidden=false;int32_t hh=8,mm=0;
void* lv_timer_get_user_data(lv_timer_t* t){return t->data;}
void get_current_time(int32_t* h,int32_t* m){*h=hh;*m=mm;}
bool lv_obj_has_flag(void*,int){return hidden;}
void update_start_time_display(alarm_sleep_24h_ui_t*){++redraws;}
void update_range_from_angles(alarm_sleep_24h_ui_t*,bool){++redraws;}
void alarm_time_up_set_origin(const char*){++rings;}
void main_ui_switch_page(const char*){}
'''+function(source,'static void time_update_timer_cb(')+r'''
int main(){
    alarm_sleep_24h_ui_t ui{reinterpret_cast<void*>(1)};lv_timer_t timer{&ui};
    time_update_timer_cb(&timer);assert(redraws==2&&rings==0);
    hidden=true;time_update_timer_cb(&timer);assert(redraws==2&&rings==0);
    assert(s_daily.Set(8,0));time_update_timer_cb(&timer);assert(rings==1);
    time_update_timer_cb(&timer);assert(rings==1);
    s_daily.Cancel();time_update_timer_cb(&timer);assert(rings==1);
}
'''
        self.compile_and_run(harness,'sleep-integration')

    def test_daily_default_cancel_repeat_boundaries_with_production_state(self):
        spec=importlib.util.spec_from_file_location('compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler();folder=ROOT/'tmp/alarm-management-cpp';folder.mkdir(exist_ok=True)
        exe=folder/'alarm.exe'
        result=subprocess.run(command+(['c++'] if kind=='zig-c++' else [])+['-std=c++17','-UNDEBUG',
            '-I',str(BOARD/'customer_ui'),str(ROOT/'tests/cpp/alarm_state_test.cc'),'-o',str(exe)],capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(subprocess.run([str(exe)],timeout=10).returncode,0)

    def test_tools_and_single_alarm_source_are_connected(self):
        tools=(BOARD/'dev_tools.cc').read_text('utf-8');sleep=(BOARD/'customer_ui/alarm_sleep_24h.cc').read_text('utf-8')
        manager=(BOARD/'customer_ui/alarm_manager.cc').read_text('utf-8')
        pomodoro=(BOARD/'customer_ui/alarm_pomodoro.cc').read_text('utf-8')
        self.assertIn('self.alarm.list',tools);self.assertIn('self.alarm.cancel',tools)
        self.assertIn('alarm_get_sleep_state',tools);self.assertIn('alarm_cancel(properties',tools)
        self.assertIn('settings.GetBool("enabled",false)',sleep)
        self.assertIn('s_daily.Due(',sleep)
        self.assertIn('RestoreOrMigrate(settings.GetInt("schema",0)',sleep)
        self.assertIn('Migrated legacy alarms: all existing alarms disabled',sleep)
        self.assertIn('settings.SetInt("schema", 1);',sleep)
        self.assertIn('settings.SetBool("enabled", s_daily.enabled);',sleep)
        self.assertIn('alarm_sleep_24h_get_state(hour,minute,enabled)',manager)
        self.assertIn('update_range_from_angles(ui, true);  /* Display toggle must not arm an alarm. */',sleep)
        self.assertIn('/* The delegated operations own the LVGL lock. Do not hold it across them. */',manager)
        self.assertIn('/* Update UI after releasing the lock; the helper acquires it when needed. */',sleep)
        self.assertIn('alarm_sleep_24h_update_ui_internal(end_hour, end_min);',sleep)
        self.assertLess(sleep.index('esp_lv_adapter_unlock();\n\n    /* Update UI after releasing the lock'),
                        sleep.index('alarm_sleep_24h_update_ui_internal(end_hour, end_min);'))
        # The timer callback must advance even when the UI is on another page.
        self.assertNotIn('return;\n    }\n\n    if (ui->remaining_seconds > 0)', pomodoro)
        self.assertIn('ui_bridge_get_current_page()', pomodoro)
        callback=sleep[sleep.index('static void time_update_timer_cb'):sleep.index('static void center_btn_event_handler')]
        self.assertIn('if (lv_obj_has_flag(ui->container, LV_OBJ_FLAG_HIDDEN))',callback)
        self.assertIn('update_range_from_angles(ui, true);',callback)


if __name__=='__main__':unittest.main()
