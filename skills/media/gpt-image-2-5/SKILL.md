---
name: gpt-image-2-5
description: Create or edit images with GPT Image 2.5.
license: MIT
metadata:
  hermes:
    tags: [Image, Generation, Editing, Identity, Style]
    related_skills: []
    homepage: https://github.com/AlekseiUL/gpt-image-2-5-agent-kit
---

# GPT Image 2.5

Use the native `image_generate` tool for a requested image or edit after the
current profile and platform have explicitly enabled the `image_gen` toolset
and selected the `openai-codex` provider. If the tool is absent, explain that
the owner must enable Image Generation for this exact profile/platform in the
Korra tools settings. A chat request alone does not enable it.

## When to Use

- The user asks to create an illustration, photo, cover, thumbnail, product
  visual, infographic, or other raster image.
- The user asks to edit an existing image, preserve a person's identity, apply
  a visual style, place a logo, or follow a reference layout.

Do not use it to edit SVG/code-native artwork or to claim that an untested
backend option is available on every ChatGPT account.

## Model and Output

Choose `gpt-image-2.5-sunburst` for precise editing, identity, layout, or exact
text work. Choose `gpt-image-2.5-flare` for fast everyday generation. Use the
quality, size, background, output format, and compression requested by the
user. Transparent output requires PNG or WebP. Compression applies only to
JPEG or WebP.

Each call requests one image and has no automatic retry or model/provider
fallback. Report a failure instead of silently spending another attempt.
Generated files and optional sanitized receipts remain under the active
profile's image cache.

## References and Identity

Pass the image being edited as `image_url`. Pass other images through
`reference_image_urls`, with one matching `reference_roles` item per reference:

- `identity` preserves distinguishing facial or character features;
- `style` supplies palette, material, lighting, and visual density;
- `logo` preserves the mark, proportions, colors, and lettering;
- `layout` supplies spatial arrangement and scale;
- `general` is limited to the purpose stated in the prompt.

For a reusable identity or style, retain the user-approved source images in
the current profile's files and reuse those same paths and roles in later
calls. Never copy a reference pack into another profile. Korra does not ship
the external kit's named-library commands; the profile-local files and roles
are the supported workflow.

For edits, state the change and pass concrete invariants in `preserve`. A mask
must be an alpha PNG with the same dimensions as the edit base; transparent
pixels identify the requested edit region. The mask guides the model and does
not guarantee pixel-perfect boundaries.

## Prompt Presets

Use only presets that improve the user's request: `portrait`, `likeness`,
`thumbnail`, `product`, `no-text`, `russian-text`, `edit`, or `brand-style`.
Do not combine `no-text` and `russian-text`. Quote literal in-image copy and
specify its placement, type direction, contrast, case, punctuation, and line
breaks. Inspect spelling, likeness, preservation, and layout before presenting
the result as approved.

## Limits

The ChatGPT/Codex image backend is account-dependent. Local validation proves
only that the request is well formed. Sunburst/Flare access, custom dimensions,
masks, transparency, compression, and higher quality levels remain unverified
for an account until its backend accepts that exact combination. The provider
does not use API keys or tokens from environment variables, files, or commands;
it reads only the active profile's normal Korra Codex OAuth login.
