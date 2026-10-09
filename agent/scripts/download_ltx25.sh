#!/usr/bin/env bash
# Download the LTX-2.5 distilled-pipeline weights (~71 GB) after accepting the license at
# https://huggingface.co/Lightricks/LTX-2.5 (gated, auto-approved on click).
set -euo pipefail
DEST=${1:-/scratch1/home/zhicao/models/ltx-2.5}
mkdir -p "$DEST"
# note: `hf download --include` has dropped the first pattern in the past, hence the dummy "x" first
hf download Lightricks/LTX-2.5 --local-dir "$DEST" --include x \
  "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors" \
  "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors" \
  "vae/ltx-2.5-video-vae-bf16.safetensors" \
  "vae/ltx-2.5-video-vae-conv-bf16.safetensors" \
  "vae/ltx-2.5-audio-vae-bf16.safetensors" \
  "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors" \
  "model_patches/ltx-2.5-duration-head-bf16.safetensors"
du -sh "$DEST"
