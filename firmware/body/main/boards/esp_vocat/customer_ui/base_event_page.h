#ifndef DESKBOT_BASE_EVENT_PAGE_H
#define DESKBOT_BASE_EVENT_PAGE_H

#include "lvgl.h"

#define PAGE_BASE_EVENTS "BASE_EVENTS"

#ifdef __cplusplus
extern "C" {
#endif

lv_obj_t *base_event_page_create_with_parent(lv_obj_t *parent);

#ifdef __cplusplus
}
#endif

#endif /* DESKBOT_BASE_EVENT_PAGE_H */
