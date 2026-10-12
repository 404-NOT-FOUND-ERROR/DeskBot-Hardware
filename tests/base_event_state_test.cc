#include "base_event_state.h"

#include <cassert>

int main()
{
    BaseEventStateTracker state;

    assert(!state.online());
    assert(state.HandleHeartbeat(1000));
    assert(state.online());
    assert(state.last_heartbeat_ms() == 1000);
    assert(!state.HandleHeartbeat(1200));
    assert(!state.CheckHeartbeatTimeout(2100, 1000));
    assert(state.CheckHeartbeatTimeout(2201, 1000));
    assert(!state.online());
    assert(state.HandleHeartbeat(2300));
    assert(!state.HandleHeartbeat(2400));

    assert(state.UpdateMechanicalStatus(0));
    assert(!state.UpdateMechanicalStatus(0));
    assert(state.UpdateMechanicalStatus(1));
    assert(!state.UpdateMechanicalStatus(1));

    state.Reset();
    assert(!state.online());
    assert(!state.mechanical_status_valid());
    return 0;
}
