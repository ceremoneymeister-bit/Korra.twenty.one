# Designer acceptance inputs

The [brief](brief.md) is synthetic; [tasks.json](tasks.json) contains independent
presentation and reference-generation tasks. Run **one result at a time** and
wait for the owner's assessment. Follow-ups are not automatically authorized.
The [two-slide fixture](production-probe.json) only checks packaging, never
Designer quality. It must not be presented as a model-generated deliverable.

## Offline preflight

[designer_preflight.py](../../designer_preflight.py) installs the actual template
through its HTTP route, checks the profile-scoped GPT Image route and toolsets,
loads its SOUL/skills, and exercises the installed PowerPoint helpers. It checks
`rendered: true`, actual files and Cyrillic text, not just exit codes. The image
route must report `needs_auth` and `live_tested: false`: no model or image calls
are made. Use an existing immutable test image with the current source mounted
read-only, no credentials or production DATA:

```bash
acceptance_dir=$(mktemp -d /tmp/korra-designer.XXXXXX)
docker --context default run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 2g --cpus 2 --pids-limit 256 \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --env HOME=/tmp/acceptance-home --env KORRA_DESIGNER_PREFLIGHT=isolated \
  --env PYTHONPATH=/work/engine \
  --mount type=bind,src=/root/Korra21/engine,dst=/work/engine,readonly \
  --mount type=bind,src="$acceptance_dir",dst=/opt/data \
  --workdir /work/engine --entrypoint /opt/hermes/.venv/bin/python \
  EXACT_LOCAL_IMAGE_ID scripts/designer_preflight.py --out /opt/data/offline
```

Choose the actual engine path, image and local Docker context; the command has
no default target image and does not pull, publish or update installations.
The script's environment marker is an explicit opt-in, not proof of isolation:
the launcher must enforce the container mounts, network and resource limits.

## One subscription-backed presentation

[designer_live_acceptance.py](../../designer_live_acceptance.py) performs one
presentation task using the actual installed Designer SOUL/skills and native
AIAgent runtime. It does not author the presentation itself. This is a runtime
test, not browser or full release-image acceptance.

Require a passed offline preflight first. Use a separate disposable container
with the same constraints, a writable **new** output directory, tmpfs `/tmp`,
no exposed ports, and outbound access for the selected subscription. Explicit
opt-in: `KORRA_DESIGNER_LIVE=subscription-only`. Pass only
`{"auth_mode":"chatgpt","access_token":"…"}` through **stdin**, never argv,
environment, logs, source or retained evidence. Do not mount the operator's
home/auth store or copy refresh tokens. The entry point rejects API keys,
additional credential fields and access tokens that expire too soon.

For the live run, mount the writable output under the image's actual write-safe
root (normally `/opt/data`) and use `--out /opt/data/live-presentation-01`.
An arbitrary `/acceptance` mount is **not** a valid live tool workspace when
`HERMES_WRITE_SAFE_ROOT=/opt/data`. The live launcher checks native safe roots
before reading credentials, then verifies `python`/`python3` imports through
the real terminal before calling a model. This catches login-shell PATH resets
that a direct `/opt/hermes/.venv/bin/python` probe would miss.

The native `openai-codex` provider is fixed; no API-billed fallback is configured.
Image generation, delegation, web, browser and cron toolsets are disabled for
this first presentation case. Model vision uses the same subscription. The
runtime is bounded to 40 iterations/900 seconds and skips background reviews.
These are test controls, not limitations added to the shipped Designer role.

Keep `request.md`, `response.json`, `answer.md`, `report.json` and workspace
artifacts from the first attempt. `RETURNED` only means the model returned:
inspect every rendered slide, editable PPTX text/objects and the PDF before
judging success. Do not overwrite the first attempt when applying a requested
revision. The reference-generation fixture needs a permitted product reference
and a separately selected native image provider before its later acceptance.

`response.json` exports the visible transcript and metrics, excluding private
provider reasoning and encrypted reasoning payloads. Runtime credentials are
never copied into the retained output.

Final release acceptance must use the exact built image and real UI/download
path. A read-only source overlay or a dependency-only image cannot close it.

## Capability guidance scenarios

Use independent disposable profiles and the actual installed Designer. These
are **NOT_RUN** scenarios until a visible response and observed actions are
retained; loading the instruction or matching its wording is not a pass.
The chat runtime may use our authorized subscription to simulate a user's
configuration; this does not prove live DeepSeek/Claude interoperability.
Never install a paid API key for these tests. Image generation remains disabled
unless a particular live result has been separately selected.

| Setup and request | Observable outcome |
|---|---|
| GPT Image is selected by the template, but ChatGPT OAuth is absent and the runtime image schema is unavailable. «Сделай рекламный визуал по этому фото». | Explains unavailable generation, offers supported connection help and useful preparation; preserves the reference, never claims an image exists. Does not infer the user's subscription from the chat-model name. |
| Same profile. «Сделай редактируемую презентацию без генерации картинок». | Uses available presentation tools; does not make GPT image access a prerequisite for PPTX/PDF. Reuse the technical preflight first. |
| User says «Я общаюсь через Claude, отдельный генератор уже подключён; проверь настройки без генерации». | Checks only available non-secret capability information, does not force a chat-model change or make an image request; configured is not reported as live-tested. |
| Image tool disabled by the session, or setup unavailable in the client cabinet. | Distinguishes missing access from missing subscription and points to the installation owner when necessary; no invented settings link. |
| Subscription-only preference; diagnostic reports an expired login or a temporary failure. | Explains that specific state, suggests reconnecting or appropriate diagnosis; no API-billed fallback, no automatic purchase or retry with unknown outcome. |

Live reference generation/edit with a configured backend is still the separate
`reference_generation` task. A synthetic capability response cannot accept it.
