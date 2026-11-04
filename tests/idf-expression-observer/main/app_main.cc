#include "expression_runtime_observer.h"

extern "C" void app_main(void)
{
#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    auto& observer = ExpressionRuntimeObserver::GetInstance();
    observer.Observe("happy", 1);
    ExpressionRuntimeObserver::Snapshot snapshot{};
    observer.TryRead(&snapshot, 2);
    (void)observer.Describe(3);
    observer.Clear();
#endif
}
