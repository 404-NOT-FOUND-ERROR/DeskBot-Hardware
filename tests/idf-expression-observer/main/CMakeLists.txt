set(SOURCES "app_main.cc")
if(CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE)
    list(APPEND SOURCES
        "${CMAKE_CURRENT_LIST_DIR}/../../../firmware/body/main/display/expression_runtime_observer.cc")
endif()

idf_component_register(
    SRCS ${SOURCES}
    INCLUDE_DIRS
        "."
        "${CMAKE_CURRENT_LIST_DIR}/../../../firmware/body/main/display"
)
