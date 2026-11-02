#!/usr/bin/env python3
"""Compare IQ down projections on shared activations without qualifying a build."""

import argparse
import subprocess
import sys
from pathlib import Path

REVISION = "c62d5e3a15b40ea51c96aae9e8d36928965f53ab"
SOURCE = "tests/dsv41/iq_expert_test.cpp"
ANCHOR = '                report("wmma vs scalar y", type, wy, 3e-3);'
DIAGNOSTIC = r'''
                // Diagnostic only: retain the original gate and compare both shared inputs.
                float *mx, *sx;
                CK(hipMalloc(&mx, (size_t) NR * E * sizeof(float)));
                CK(hipMalloc(&sx, (size_t) NR * E * sizeof(float)));
                iq::SetArgs cross_m = qm, cross_s = qs;
                cross_m.xh = sh; cross_m.y = mx;
                cross_s.xh = mh; cross_s.y = sx;
                if (!iq::hipk_force(1)) return 2;
                const long long cross_n = iq::hipk_launches();
                if (!iq::set_down(cross_m, 0)) return 2;
                CK(hipDeviceSynchronize());
                const long long cross_taken = iq::hipk_launches() - cross_n;
                iq::hipk_force(0);
                const long long scalar_n = iq::hipk_launches();
                if (!iq::set_down(cross_s, 0)) return 2;
                CK(hipDeviceSynchronize());
                const long long scalar_taken = iq::hipk_launches() - scalar_n;
                iq::hipk_force(-1);
                if (cross_taken < 1 || scalar_taken != 0) {
                    std::printf("diagnostic paths did not run as forced\n");
                    return 2;
                }
                const auto cross_ym = down_(mx, (size_t) NR * E);
                const auto cross_ys = down_(sx, (size_t) NR * E);
                double same_m = 0, same_s = 0, cpu_sm = 0, cpu_ss = 0, cpu_ms = 0;
                for (int e = 0; e < 3; ++e)
                    for (int lr = bounds[e]; lr < bounds[e + 1]; ++lr) {
                        std::vector<float> hmf(FF), hsf(FF);
                        for (int c = 0; c < FF; ++c) {
                            hmf[c] = h2f(hm[(size_t) lr * FF + c]);
                            hsf[c] = h2f(hs[(size_t) lr * FF + c]);
                        }
                        for (int r = 0; r < E; ++r) {
                            double sm, ss;
                            const auto *weights = &D[e].w[(size_t) r * FF];
                            const double rm = dotd(weights, hmf.data(), FF, &sm);
                            const double rs = dotd(weights, hsf.data(), FF, &ss);
                            const size_t j = (size_t) lr * E + r;
                            same_m = std::max(same_m, std::fabs((double) ym[j] - cross_ys[j]) / (sm + 1e-30));
                            same_s = std::max(same_s, std::fabs((double) cross_ym[j] - ys[j]) / (ss + 1e-30));
                            cpu_sm = std::max(cpu_sm, std::fabs((double) cross_ys[j] - rm) / (sm + 1e-30));
                            cpu_ss = std::max(cpu_ss, std::fabs((double) ys[j] - rs) / (ss + 1e-30));
                            cpu_ms = std::max(cpu_ms, std::fabs((double) cross_ym[j] - rs) / (ss + 1e-30));
                        }
                    }
                report("DIAG same WMMA h", type, same_m, 3e-3);
                report("DIAG same scalar h", type, same_s, 3e-3);
                report("DIAG scalar(W h)/CPU", type, cpu_sm, 3e-3);
                report("DIAG scalar(S h)/CPU", type, cpu_ss, 3e-3);
                report("DIAG WMMA(S h)/CPU", type, cpu_ms, 3e-3);
                CK(hipFree(mx)); CK(hipFree(sx));
'''


def instrument(source):
    text = source.decode("utf-8")
    if text.count(ANCHOR) != 1:
        raise ValueError("Diagnostic insertion point changed; refusing to modify the test.")
    return text.replace(ANCHOR, ANCHOR + DIAGNOSTIC).encode("utf-8")


def diagnose(root):
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("Source revision differs from the reviewed diagnostic contract.")
    source = root / SOURCE
    original = source.read_bytes()
    pristine = subprocess.check_output(["git", "-C", str(root), "show", f"{REVISION}:{SOURCE}"])
    if original != pristine:
        raise ValueError("Test source has local changes; refusing to overwrite them.")
    modified = instrument(original)
    build = [str(root / ".venv/bin/cmake"), "--build", str(root / "build-hip"), "--target", "ds4_iq_test", "-j", "1"]
    results = []
    try:
        source.write_bytes(modified)
        subprocess.run(build, check=True)
        executable = str(root / "build-hip/ds4_iq_test")
        for options in ([], ["--type", "23"]):
            print(f"\nDiagnostic test: {options or 'all default types'}", flush=True)
            result = subprocess.run([executable, *options], check=False, timeout=600)
            results.append(result.returncode)
            print(f"Diagnostic exit status: {result.returncode}", flush=True)
    finally:
        source.write_bytes(original)
        print("Restored original test source; rebuilding the original test binary.", flush=True)
        subprocess.run(build, check=True)
    print("Diagnostic only: no native engine rebuild, receipt, or qualification change.", flush=True)
    return 1 if any(results) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on the Ubuntu gfx1151 host with other inference engines stopped.")
    try:
        return diagnose(args.directory.expanduser().resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    sys.exit(main())
