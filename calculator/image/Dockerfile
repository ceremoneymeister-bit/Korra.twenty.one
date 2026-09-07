ARG ENGINE_REF=ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:02ea3ebc19051915ac220111c800bf6e12ff427d557f0f0310bfceb8d216f900
ARG CAD_REF=korra-calculator:v9-f383ea8651-a1
FROM ${CAD_REF} AS cad_source
FROM ${ENGINE_REF} AS kernel
USER root
COPY requirements.lock /tmp/calc-requirements.lock
RUN uv venv --python /opt/hermes/.venv/bin/python /opt/metal-calc/.venv && \
    uv pip sync --python /opt/metal-calc/.venv/bin/python --require-hashes /tmp/calc-requirements.lock
COPY metal_calc/ /tmp/metal-calc-source/
RUN uv build --wheel --no-build-isolation --python /opt/hermes/.venv/bin/python \
      --out-dir /tmp/calc-wheels /tmp/metal-calc-source && \
    uv pip install --python /opt/metal-calc/.venv/bin/python --no-deps /tmp/calc-wheels/*.whl && \
    rm -rf /tmp/metal-calc-source /tmp/calc-wheels
COPY --from=cad_source /opt/metal-calc/libredwg/ /opt/metal-calc/libredwg/
COPY lib/ /opt/metal-calc/lib/
COPY bin/ /opt/metal-calc/bin/
COPY review/order_schema.json /etc/metal-calc/order_schema.json
COPY check_versions.py check_tool_surface.py /opt/metal-calc/acceptance/
COPY fixtures/ /opt/metal-calc/acceptance/
RUN chmod 0755 /opt/metal-calc/bin/* && \
    /opt/metal-calc/.venv/bin/python /opt/metal-calc/acceptance/check_versions.py && \
    /opt/metal-calc/.venv/bin/python /opt/metal-calc/acceptance/check_tool_surface.py && \
    /opt/metal-calc/bin/dwg2dxf --version && \
    chmod -R a-w /opt/metal-calc /etc/metal-calc
ENV CADKIT_DWG2DXF=/opt/metal-calc/bin/dwg2dxf

FROM kernel AS pilot
COPY runtime-overlay/ /opt/hermes/
COPY web-dist/ /opt/hermes/korra_cli/web_dist/
COPY handoff/runner.py /opt/metal-calc/lib/handoff_runner.py
COPY source-receipt.json /opt/metal-calc/receipts/source-receipt.json
RUN mkdir -p /etc/s6-overlay/s6-rc.d/calc-handoff/dependencies.d && \
    printf 'longrun\n' > /etc/s6-overlay/s6-rc.d/calc-handoff/type && \
    printf '%s\n' '#!/command/with-contenv sh' \
      'if [ "${METAL_CALC_AUTORUN:-0}" != 1 ]; then exec sleep infinity; fi' \
      'exec s6-setuidgid hermes /opt/metal-calc/.venv/bin/python /opt/metal-calc/lib/handoff_runner.py' \
      > /etc/s6-overlay/s6-rc.d/calc-handoff/run && \
    chmod 0755 /etc/s6-overlay/s6-rc.d/calc-handoff/run && \
    touch /etc/s6-overlay/s6-rc.d/calc-handoff/dependencies.d/base && \
    touch /etc/s6-overlay/s6-rc.d/user/contents.d/calc-handoff && \
    /opt/metal-calc/.venv/bin/python -m py_compile /opt/metal-calc/lib/handoff_runner.py && \
    chmod -R a-w /opt/metal-calc /etc/metal-calc
LABEL org.opencontainers.image.title="Korra 21 Calculator" \
      ru.korra.calculator.engine-revision="c9c42ecb375c56f853576f0d4a06519deeb96f78" \
      ru.korra.calculator.kernel="v9" \
      ru.korra.calculator.stage="internal-pilot"
