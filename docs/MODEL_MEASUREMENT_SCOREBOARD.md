# Model measurement scoreboard

Open Operations, Scripts & TPS tests, Model scoreboard. Completed benchmark jobs appear automatically, including older jobs beyond the recent 100-job list. New measurements persist in the configured host jobs_directory and survive dashboard rebuilds and restarts. Back up that directory; removing it removes the measurement history.

## Compare measurements

Filter by backend or model and sort by date, end-to-end TPS, native decode TPS or TTFT. The scoreboard pages through all saved completed measurements, 100 per page. Inspect opens the original job with individual samples and result JSON; Export result JSON there saves the full record. Export this page JSON saves the current scoreboard page.

Rates and token counts are medians across measured samples. Warmup is excluded. End-to-end TPS divides generated tokens by client elapsed time and includes queueing, loading and prefill. Native decode and prefill rates require backend timing support and remain unknown when unavailable. TTFT measures time until the first content or reasoning chunk.

New runs save output limits, warmup, temperature, seed, thinking mode, a SHA-256 prompt fingerprint, and a snapshot of runtime context, kernel and CPU. Ollama reports installed artifact size and digest when available. Optional fields record model artifact size in GiB, quantization, engine revision, and notes. User supplied values are labelled as declared; unavailable metadata stays unknown. The post-sample memory snapshot is neither peak usage nor model size. Model artifact size excludes KV cache, graph buffers and other runtime allocations.

Use the same prompt fingerprint, actual token counts, output cap, thinking mode, sampling settings and hardware when comparing runs. Allocated context differs from actual prompt length. A short prompt at 256K allocation does not measure prefill or decode with 256K tokens filled. Caches are not cleared between runs; concurrent inference and thermal conditions can affect results. Use notes to record these conditions, speculative decoding, power settings and model variants.

## Import previous script output

Expand Import earlier TPS script JSON lines. Choose backend and model, paste one measured sample per line, then click Import measurements. Each sample requires positive elapsed_s and output_tokens. Omit warmup runs. Optional metrics include ttft_s, prompt_tokens, decode_tps and prefill_tps. End-to-end TPS is recomputed from the supplied token count and elapsed time. Imports retain unknown warmup and sampling settings rather than assigning current defaults. Repeating identical samples and declared settings returns the existing measurement.

The original three DwarfStar V4 Flash script runs from this conversation are preserved in docs/benchmarks/dwarfstar-v4-flash-original.jsonl. Paste those lines into the importer with backend dwarfstar and model deepseek-v4-flash. They have a median of approximately 15.81 end-to-end TPS and 0.5143 seconds TTFT, with 15 prompt tokens and 1439 output tokens per run. Their allocation, warmup and sampling parameters were not included in the pasted records, so leave those unknown unless separately confirmed.

The later 15.41 TPS result at 262144 allocated context was reported only as a summary. Its raw samples and TTFT were not provided. Keep that observation in notes until the script output is available; do not manufacture individual samples. The 80.76 GiB reported by DwarfStar was resident model memory, not a verified artifact size.

## Update the deployment

Deploy the updated dashboard and host helper from the same revision using the existing rebuild procedure. No extra database or package is needed. Existing jobs_directory settings are reused. Scoreboard reads do not load a model or make inference requests. Only a newly started benchmark queries the selected backend; runtime metadata is collected outside the timed sample.
