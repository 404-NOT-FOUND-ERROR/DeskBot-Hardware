#ifndef DESKBOT_MAGNETIC_TELEMETRY_PAGE_H
#define DESKBOT_MAGNETIC_TELEMETRY_PAGE_H

#include "lvgl.h"

#define PAGE_MAGNETIC_TELEMETRY "MAGNETIC_TELEMETRY"

#ifdef __cplusplus
extern "C" {
#endif

lv_obj_t *magnetic_telemetry_page_create_with_parent(lv_obj_t *parent);

#ifdef __cplusplus
}
#endif

#endif /* DESKBOT_MAGNETIC_TELEMETRY_PAGE_H */
