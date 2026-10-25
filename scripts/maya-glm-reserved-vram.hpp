#pragma once

#include <algorithm>
#include <cstddef>

namespace odysseus_glm {

// Only for a verified large firmware reservation excluded from Linux MemTotal.
inline size_t reserved_pool_budget(size_t vram_total, size_t vram_used, size_t device_free,
                                  size_t ram_total, size_t ram_available, size_t reserve,
                                  size_t ram_headroom, size_t experts, size_t scratch) {
    constexpr size_t GiB = size_t{1} << 30;
    if (vram_total < 64 * GiB || vram_total <= ram_total || vram_used > vram_total ||
        ram_available > ram_total || ram_available <= ram_headroom ||
        ram_available - ram_headroom < reserve) return 0;
    size_t room = std::min(device_free, vram_total - vram_used);
    room = room > reserve ? room - reserve : 0;
    room = room > scratch ? room - scratch : 0;
    return std::min(experts, room);
}

}  // namespace odysseus_glm
