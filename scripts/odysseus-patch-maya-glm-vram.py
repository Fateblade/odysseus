#!/usr/bin/env python3
"""Add opt-in verified Linux reserved-VRAM budgeting to isolated Maya v1.1.22."""

import argparse
import hashlib
import os
from pathlib import Path

SOURCE_SHA256 = "e744f217ed09b1242d4b28f57310445aa28b2f6f00c516f5e74a3c7cbd75ae17"
INCLUDE = '#include "glm_fast_state.hpp"\n'
EXTRA_INCLUDE = '#include "odysseus_glm_reserved_vram.hpp"\n'
ANCHOR = '            std::fprintf(stderr, "glm fast: HIP%d unified memory: MemAvailable %.2f GB, headroom %.2f GB + "'
INSERT = r'''            const char* reserved_mode = getenv("STRATA_GLM_RESERVED_VRAM");
            if (reserved_mode && std::string(reserved_mode) != "0") {
                if (std::string(reserved_mode) != "1") {
                    err = "GLM reserved VRAM mode must be 0 or 1";
                    return false;
                }
#if defined(__linux__)
                char bus[32] = {};
                if (cudaDeviceGetPCIBusId(bus, sizeof bus, dev_) != cudaSuccess) {
                    err = "GLM reserved VRAM: cannot identify the active GPU PCI device";
                    return false;
                }
                std::string pci(bus);
                for (char& ch : pci) ch = (char) std::tolower((unsigned char) ch);
                const std::string sys = "/sys/bus/pci/devices/" + pci + "/";
                size_t vram_total = 0, vram_used = 0, ram_total = 0;
                std::ifstream(sys + "mem_info_vram_total") >> vram_total;
                const bool used_read = bool(std::ifstream(sys + "mem_info_vram_used") >> vram_used);
                std::ifstream meminfo("/proc/meminfo");
                std::string key, rest;
                size_t kb = 0;
                while (meminfo >> key >> kb) {
                    std::getline(meminfo, rest);
                    if (key == "MemTotal:") { ram_total = kb * 1024; break; }
                }
                avail = used_read ? odysseus_glm::reserved_pool_budget(vram_total, vram_used, free_b,
                    ram_total, (size_t) std::max(0LL, avail_kb) * 1024, reserve,
                    (size_t) (head_gb * 1073741824.0), expert_bytes,
                    pf_ != nullptr ? mmq_scratch_reserve() : 0) : 0;
                if (ram_total == 0 || avail == 0) {
                    err = "GLM reserved VRAM: reservation/headroom verification failed; no pool override applied";
                    return false;
                }
                std::fprintf(stderr, "glm fast: HIP%d reserved VRAM mode: PCI %s, total %.2f GB, free %.2f GB; "
                                     "Linux RAM %.2f GB, headroom %.2f GB; expert pool budget %.2f GB\n",
                             dev_, pci.c_str(), (double) vram_total / 1073741824.0,
                             (double) (vram_total - vram_used) / 1073741824.0,
                             (double) ram_total / 1073741824.0, head_gb,
                             (double) avail / 1073741824.0);
#else
                err = "GLM reserved VRAM mode requires Linux sysfs verification";
                return false;
#endif
            }
'''


def patch_source(raw):
    source = raw.replace(b"\r\n", b"\n").decode("utf-8")
    original = source.replace(EXTRA_INCLUDE, "").replace(INSERT, "")
    if hashlib.sha256(original.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError("GLM source differs from pinned v1.1.22; preserve edits and inspect before patching.")
    if original.count(INCLUDE) != 1 or original.count(ANCHOR) != 1:
        raise ValueError("Unexpected GLM memory planner; refusing to patch.")
    patched = original.replace(INCLUDE, INCLUDE + EXTRA_INCLUDE).replace(ANCHOR, INSERT + ANCHOR)
    if source not in (original, patched):
        raise ValueError("Partial or modified reserved-VRAM patch; preserve and inspect it first.")
    return patched.encode()


def atomic_write(path, raw):
    temporary = path.with_name(path.name + ".reserved-vram.tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(raw)
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Isolated project-maya-glm53 checkout")
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    source = root / "src/core/glm_fast_path.cu"
    header = source.with_name("odysseus_glm_reserved_vram.hpp")
    backup = source.with_name("glm_fast_path.cu.before-odysseus-vram")
    try:
        if any(path.is_symlink() or not path.resolve().is_relative_to(root) for path in (source, header, backup)):
            raise ValueError("Patch files must not be symlinks.")
        raw = source.read_bytes()
        patched = patch_source(raw)
        header_bytes = Path(__file__).with_name("maya-glm-reserved-vram.hpp").read_bytes().replace(b"\r\n", b"\n")
        if header.exists() and header.read_bytes().replace(b"\r\n", b"\n") != header_bytes:
            raise ValueError("Existing reserved-VRAM header differs; preserve it before continuing.")
        if backup.exists():
            if hashlib.sha256(backup.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != SOURCE_SHA256:
                raise ValueError("Existing GLM backup differs from the pinned original.")
        else:
            if hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() != SOURCE_SHA256:
                raise ValueError("Patched source has no verified original backup.")
            with backup.open("xb") as stream:
                stream.write(raw)
        atomic_write(header, header_bytes)
        atomic_write(source, patched)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Reserved-VRAM patch installed. Rebuild maya; default allocation remains unchanged.")
    print("Opt in only in the isolated GLM config: MAYA_GLM_RESERVED_VRAM=1.")


if __name__ == "__main__":
    main()
