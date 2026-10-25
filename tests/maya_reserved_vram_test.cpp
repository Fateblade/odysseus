#include "maya-glm-reserved-vram.hpp"
#include <cassert>

int main() {
    using odysseus_glm::reserved_pool_budget;
    constexpr size_t G = size_t{1} << 30;
    // The reported 96 GiB reservation / 30 GiB Linux split can hold the experts.
    assert(reserved_pool_budget(96*G, 8*G, 88*G, 30*G, 25*G, G, 16*G, 80*G, 0) == 80*G);
    // Both GPU counters bound the allocation; scratch and reserve stay free.
    assert(reserved_pool_budget(96*G, 50*G, 88*G, 30*G, 25*G, G, 16*G, 80*G, 2*G) == 43*G);
    assert(reserved_pool_budget(96*G, 8*G, 40*G, 30*G, 25*G, G, 16*G, 80*G, 2*G) == 37*G);
    assert(reserved_pool_budget(96*G, 96*G, 88*G, 30*G, 25*G, G, 16*G, 80*G, 0) == 0);
    assert(reserved_pool_budget(96*G, 97*G, 88*G, 30*G, 25*G, G, 16*G, 80*G, 0) == 0);
    // A normal shared-memory APU, missing counters or insufficient OS headroom fails closed.
    assert(reserved_pool_budget(16*G, 0, 112*G, 120*G, 110*G, G, 16*G, 80*G, 0) == 0);
    assert(reserved_pool_budget(96*G, 0, 112*G, 128*G, 110*G, G, 16*G, 80*G, 0) == 0);
    assert(reserved_pool_budget(0, 0, 112*G, 30*G, 25*G, G, 16*G, 80*G, 0) == 0);
    assert(reserved_pool_budget(96*G, 8*G, 88*G, 30*G, 16*G, G, 16*G, 80*G, 0) == 0);
    assert(reserved_pool_budget(96*G, 8*G, 88*G, 30*G, 31*G, G, 16*G, 80*G, 0) == 0);
}
