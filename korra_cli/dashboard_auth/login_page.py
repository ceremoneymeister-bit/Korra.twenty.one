"""Server-rendered /login page.

No React, no JavaScript dependency. Listed providers come from the
registry; clicking a provider sends a GET to
``/auth/login?provider=<name>``.

Visual styling is the Korra21 "tactile threshold": the accepted light
palette (``UI_PALETTE.md``), Onest typography, restrained neumorphic
depth, and the ``21`` edition mark. The page is light-only — the owner's
09.09.2026 decision is "light by default, always, in every interface", and
a login screen has no theme switch to honour a stored choice with.

Brand marks (wordmark + ``21``) are images, not a text lockup: same PNGs
the dashboard header uses. They and the fonts are served out of the SPA's
``/brand/`` and ``/fonts/`` directories, which the dashboard-auth gate
allowlists pre-auth (see ``_GATE_PUBLIC_PREFIXES`` in ``middleware.py``),
so the page renders without needing the React bundle loaded.

Test-stable class names: the existing test suite extracts the
``class="provider-btn"`` anchor href to walk the OAuth flow. That
class name MUST NOT change without updating
``tests/korra_cli/test_dashboard_auth_401_reauth.py``.
"""
from __future__ import annotations

import html

from korra_cli.dashboard_auth import list_session_providers
from korra_cli.dashboard_auth.prefix import normalise_prefix

# Inline minimal CSS. The dashboard's full skin lives in the React
# bundle, which we deliberately do NOT load here — the login page must
# not depend on the SPA build being present or on the injected session
# token.
#
# Single curly braces are placeholders for ``str.format``; CSS curlies
# are doubled (``{{`` / ``}}``).
_LOGIN_HTML_TEMPLATE = """\
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAAGYktHRAD/AP8A/6C9p5MAAAAJcEhZcwAABdAAAAXQAS2QItgAAAAHdElNRQfqCQMHEy+r3zWVAAAEJHpUWHRSYXcgcHJvZmlsZSB0eXBlIHhtcAAAWIW1WEmSrDgM3esUfQRbtmVzHCqBXUf0so/fTwLSDGb4FdFFkYMt6Wm2SPr373/oL/yxl0Lhk0voc5EhOxkk5SienX6Xj4w56F4YmMVLlElYUujn9S/1xMyOqhgs/ihLKnFIjl0MMmUwsgvCY3B2cxhdz04vqMAQLqFPHCNFOeDPm6pDyRGXCz0wp2x/PGYQ8WgQmafgQ6cXTxRcYCwwXodZCN5D7iAWaufCgwLodtXlqFGMkigHCVjozLQOXhih4UIAvzCw4QnVEI7YCdpeuVA1Cq4rENUwy3Qaq7vxGf6TATsLj5rmzYnTPd4GLkpoeBBRSzDkU92nhIoExqQBVl9Bn8kA/TUgHRHNU/JnBioPKdOC9wcGzpm3BaNHtIIkcM9QJ9OiNwQkhPC1eZVGk0PThpA3NZjQ6OtYQz8m4ZqK31TptZCgxESZU0C5eghLYUR2JlwB96dmSSXfU29BqIWSHDK9aAqggFXxOQ3CnPmA86gZh2sDRs9sj8bZJ4Q/4T/zBg9lDEx3MG75dKReYegKZ6Nn98Y8esf2aJ4QYuHBUGZRDTP90a3Kduailr23OaO9HdRrOdvaiKiBJFsH8Gtjaws89WlkUvYXzm6zwIzOnAvz9mvNhLwVwvdCmoJOQsKzkEYeHYS4d0IWQVviowv3MKcy3kSQtiF8G0GkhB4B1pZxf7QT0JtQRy8fnG03abH4aO+btWPaNw9Ns3ZKO3Oj1iz0TSpY297gQep+0uCpJB5w1DoeIPBlhKrPqs50rbQ59+SLK5+REtrpn8zFwPifSwTNZVC9UOTwYtLhIhiwaNy1+uW3ftnrRGfC2S9vzIXW6wHfE5RM2Q6b9aiTMSFT1YxvuX7Jr6npOmaVPU52qqZlioubvv2FoTvCJ4BUqoZUVQRCF2wyOZhTWgYdYaiB07UZL2AWarrH+cLM8xCaxzaWSYcg8CPLIAhlmUWnfZuNMB/GGH0sUSOE9YCv0mPCdlm1RJMJQ0qgQC1jqCnoOxywTRGTPRbY6hxjcIqY+Uv0ySSYGQ/aXCTkbtDSToQCeFN9dN9er6uvjvU6L8E0tTvFjMkfTUIfaZaIWcvF3tXUdryoYQ7G3NiJFkhCieitc1yHwEfcGZ9SKPMJUzlpz/oG+wytjkdmi04Vq3FLmZ4Jj3SPJ+1F9Grok8xdQec6PJLhvdNXmhv+PdtvnY3TNMfF3WLDMhwsNujpiQPDomkR4XKva0hmUWfj2RGR0GAHi0bArfFBnjS03cA8jjUXfpp7FVI3/zDPlYks65m1OqlRnswf5rVEZ9JWmaJKjXKuVNJSXYh35QpRQ8Owr1YvBq3fHU70NBG96QZKR9tn9/Oj+/Z3h83RyNarsDf3K32ipO1vIrpF/wFA7t3Yuq7xWQAABWNJREFUWMPNl01sVFUUx3/nvtfpzHQ+OrSFMpQUyreoUVAMxgAJJEYTwejCRPeudKWy0Z3uXOnCuNQlLkjcKBg/IgQSAT8iny0ULIW2lHb6OdPpzHv3uLgzbZHOQBWjZ/Jm3tzkvv///M//nncv/Mch1Zuu7AbAABoHskCTiPzd594RqgowDgwCJbGW3qEr8wS6shtRVATZDbwFbANiD5jAJPAD8KGIXFBVrg70IF3ZjYgIqroX+ExEOv5NyVX1DPAa0HN1oAcvk2wBSAAfi8jjDyBT7qFcFrDW6tFlqVZMZXANTvb7QAFFUWzlUjemiucLni+o1ZrTK+R2GSMZYI5AAojejaULwCp3YmmwURJBK4mgFd82oqKohU27hOcPGjIdgq1DAkhV8fwqsbuyFMWoR9QmEDXMmgIGw/LZDawqPkIiaAEgF+nnQvIbZjRPsg227TdksvDlB5aJQUVMzXLIQgJ34ouSCFpYn3+GTLkDQZjxJkAhHWQx6jmWQPvsJgYbL5D3LjLULRQmlc7thu0vwvefhqhCPUv4iw8rDTZGc3klDTaGALEwVYFUVCyCgEJIGY3NsOOAoeMhg6lwW79T+OmQkM/VZ2AWHxbGIjc43fwFlxLfUTIzzmyiIDhw3O9I5Bo5BjHGsPpRwQYQBkpzFlo6QS1oHTv4i8MLijLjTdAcrCIWpuckX+iT8YYBehLHKNsSPx829BwPaVoG7RuEPa8btuwx3DxnsSE1S1GjBA6hpdRJe3GTQxO3KkTnn3IjepYpfxiDhypM3lImb0FxGvp+UR5+VmhMGE58bhm5rov2B7M4tCJqWDG7kQatLLPKWMkUmPJvA4LBo7qARNyXeMITLxnWbBfiaWHdU+J8sTQPgK+NpIL2OeFFDXkvx4Xkt8yaPKCUpACADRVVRQRsWbl5Tokm3bzcDWVyuLYP6xKI2DhUMi94Oc6mvmKosRsVy5Q/Qi7SjyBktwhNyyodUKCppaoI2ACsXbICDtS5XQilRHfiR0YjfVgJuNx0nPPJIxRkghXrhZff99h2QLAW1u80PP2qQTyptGjqRm0TyvzMQGaZ8ocdHRXGGwacMUNh826hpVNItwtb9xr2vWFILXdkjA/5MQhLS1RAgaZgGY22yamBR6a82vmh2gfUNZ10uxCUlOaV8Nw7huaVDlzEST/UrYSBzvWO+/aAYJj0h7ES0h/7jbGGfgS3QqphLZSLYHxhzXZDPC3z9RaYHlGuntK5//dNoNrhfk0fZtK/RabcgacRomEKT333XlQlu1nofExQy1yfEpl3/NmjynCvIjXTrNUJVbASUvDGGYxeZMvUPp4cf4VAikz7o1yJnaS04ib73jS0dQk2/MsyE7h1Rbl2xtIQFUoFXZoC1ToLwrg/QGCKRGyciMYBSJSXs26H6/1aqbcuVABoysD+9zxeeNeQaK29SanTih2TKf82E/4QraUuimaK88mjFJhk3WqDH3EvHwDjQVhWcv0QiUM8DUHZETJLLcFCLwSmyLX4KZJBG/EwQzzMkPcnuHpK2bpPSS13b7zb1+D3ry3dx5RIDBKtMJuH0etQLtbemNxDAUfjdqSXc8kjrC3swBJiDNw4qxw6GJJqE2yojPTB9KhrxwqM9FVmm/qb1CqBGaC0OLygogxFu8lFrhNIGRBUhZE+ZeSP6k4YjFfdJ9wz5vCq1ekDLtUrBUDJzGAlQFQqtRWM5646e7/F4jSQmyMgImPAR6o6Xo9E9XM/KdYKVe0FPgFCULyxqVEqh5Nu4DqwFkhX0rYP5FIsMA2cAN42njnpjmaXFx5ON4KqINIGbAZS/yTTO9N2vgQuichE9Vz4v4g/ASlFZPGB5P9IAAAAFHRFWHR4bXA6Q29sb3JTcGFjZQA2NTUzNTtUTfIAAABEdEVYdHhtcDpDcmVhdG9yVG9vbABDYW52YSBkb2M9REFISlJmYTREancgdXNlcj1VQUZzYnh3UjdyRSBicmFuZD1UIEhBU0RGcgbz3AAAABR0RVh0eG1wOkV4aWZWZXJzaW9uADAyMTCwWRvkAAAAGHRFWHR4bXA6Rmxhc2hQaXhWZXJzaW9uADAxMDCBx9h8AAAAGHRFWHR4bXA6UGl4ZWxYRGltZW5zaW9uADEyMDBXCdQ6AAAAGHRFWHR4bXA6UGl4ZWxZRGltZW5zaW9uADEyMDDu8g/SAAAAAElFTkSuQmCC">
<meta name="theme-color" content="#e8e8e8">
<title>Вход — Korra21</title>
<style>
  /* The login page uses the same accepted type and colour tokens as Korra21. */
  @font-face {{
    font-family: 'Onest';
    font-style: normal;
    font-weight: 100 900;
    font-display: swap;
    src: url('{base_path}/fonts/Onest-Variable.woff2') format('woff2');
  }}

  :root {{
    color-scheme: light;
    --canvas: #e8e8e8;
    --surface: #e0e0e0;
    --surface-inset: #dcdcdc;
    --text: #1f1f1f;
    --text-muted: #5c5c5c;
    --lime: #9ede01;
    --lime-text: #1f1f1f;
    --shadow: #bebebe;
    --highlight: #ffffff;
    --danger: #a4231c;
  }}

  *, *::before, *::after {{ box-sizing: border-box; }}

  html, body {{
    margin: 0;
    padding: 0;
    min-height: 100%;
    background: var(--canvas);
    color: var(--text);
    font-family: 'Onest', sans-serif;
    font-optical-sizing: auto;
    font-size: 16px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}

  body {{
    min-height: 100vh;
    min-height: 100svh;
    overflow-x: hidden;
    background:
      radial-gradient(circle at 16% 12%, #f4f4f4, transparent 38rem),
      var(--canvas);
  }}

  main {{
    min-height: 100vh;
    min-height: 100svh;
    display: grid;
    place-items: center;
    padding: clamp(1.5rem, 5vw, 4rem);
  }}

  .login-shell {{
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(21rem, 25rem);
    align-items: center;
    gap: clamp(3rem, 8vw, 7rem);
    width: 100%;
    max-width: 66rem;
    animation: arrive 0.55s cubic-bezier(.2,.8,.2,1) both;
  }}

  .identity {{ min-width: 0; }}

  @keyframes arrive {{
    from {{ opacity: 0; transform: translateY(12px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}

  /* Both PNGs have transparent padding. The new shadow-free letters fill
     58.3% of their frame height, the numeral 67.8%; .86 keeps their visible
     heights aligned. The optical gap is already in the files. */
  .brand-lockup {{
    --word-h: clamp(2.55rem, 4.9vw, 3.75rem);
    display: flex;
    align-items: center;
    gap: 0.1rem;
    margin-bottom: clamp(2.6rem, 8vh, 4.5rem);
  }}

  .brand-word {{ height: var(--word-h); width: auto; }}
  .brand-number {{ height: calc(var(--word-h) * 0.86); width: auto; }}

  /* Строка слева меняется сама. Стек на grid, а не абсолютное
     позиционирование: блок получает высоту самой длинной фразы, и раскладка
     не прыгает при смене. Анимация только на CSS — OAuth-вариант этой
     страницы не грузит скрипты вообще. */
  h1 {{
    display: grid;
    max-width: 20ch;
    margin: 0;
    font-size: clamp(1.9rem, 3.4vw, 2.9rem);
    font-weight: 700;
    line-height: 1.08;
    letter-spacing: -0.045em;
    text-wrap: balance;
  }}

  .tagline-line {{
    grid-area: 1 / 1;
    opacity: 0;
    animation: tagline 30s cubic-bezier(.2,.8,.2,1) infinite;
  }}

  .tagline-line:nth-child(1) {{ animation-delay:  0s; }}
  .tagline-line:nth-child(2) {{ animation-delay:  6s; }}
  .tagline-line:nth-child(3) {{ animation-delay: 12s; }}
  .tagline-line:nth-child(4) {{ animation-delay: 18s; }}
  .tagline-line:nth-child(5) {{ animation-delay: 24s; }}

  /* Окно показа 22 % при шаге 20 % — фразы перекрываются на 0,6 с и
     переливаются одна в другую. Без перекрытия между ними остаётся пустой
     кадр, и смена читается как мигание. */
  /* Запрет анимаций снимает движение, а не смену фраз: то же расписание,
     но без сдвига и размытия. */
  @keyframes tagline-plain {{
    0%    {{ opacity: 0; }}
    2%    {{ opacity: 1; }}
    20%   {{ opacity: 1; }}
    22%   {{ opacity: 0; }}
    100%  {{ opacity: 0; }}
  }}

  @keyframes tagline {{
    0%    {{ opacity: 0; transform: translateY(0.28em);  filter: blur(7px); }}
    2%    {{ opacity: 1; transform: translateY(0);       filter: blur(0); }}
    20%   {{ opacity: 1; transform: translateY(0);       filter: blur(0); }}
    22%   {{ opacity: 0; transform: translateY(-0.28em); filter: blur(7px); }}
    100%  {{ opacity: 0; }}
  }}

  .card {{
    position: relative;
    padding: clamp(1.6rem, 4vw, 2.4rem);
    border-radius: 1.9rem;
    background: var(--surface);
    box-shadow: 5px 5px 10px var(--shadow), -5px -5px 10px var(--highlight);
  }}

  h2 {{
    margin: 0 0 1.9rem;
    font-size: clamp(1.7rem, 2.8vw, 2.15rem);
    font-weight: 680;
    line-height: 1.1;
    letter-spacing: -0.035em;
    text-wrap: balance;
  }}

  .provider-list {{
    display: grid;
    gap: 0.75rem;
  }}

  .provider-btn {{
    min-height: 3.35rem;
    display: grid;
    place-items: center;
    width: 100%;
    padding: 0.85rem 1.2rem;
    text-align: center;
    background: var(--lime);
    color: var(--lime-text);
    font: inherit;
    font-size: 0.95rem;
    font-weight: 720;
    text-decoration: none;
    border: 0;
    border-radius: 1rem;
    cursor: pointer;
    box-shadow: 3px 3px 6px var(--shadow), -3px -3px 6px var(--highlight);
    transition: transform 0.12s ease, filter 0.12s ease, box-shadow 0.12s ease;
  }}

  .provider-btn:active {{
    transform: translateY(1px);
    box-shadow: inset 2px 2px 6px rgba(31, 31, 31, 0.28), inset -2px -2px 6px rgba(255, 255, 255, 0.5);
  }}

  /* Активную зону показываем глубиной, а не обводкой (канон палитры, 03.09). */
  .provider-btn:focus-visible {{
    outline: none;
    box-shadow: inset 2px 2px 6px rgba(31, 31, 31, 0.28), inset -2px -2px 6px rgba(255, 255, 255, 0.5);
  }}

  .provider-btn:disabled {{
    cursor: wait;
    filter: saturate(0.7) brightness(0.94);
  }}

  .provider-form {{
    display: grid;
    gap: 0.75rem;
    text-align: start;
  }}

  .field {{ display: grid; }}

  /* Подпись поля остаётся для скринридера, визуально её роль играет
     placeholder — так устроены входы ИИ-сервисов, к которым владелец
     попросил приблизиться 09.09.2026. */
  .field-label {{
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }}

  .field-input {{
    min-height: 3.35rem;
    width: 100%;
    padding: 0.85rem 1.1rem;
    background: var(--surface-inset);
    color: var(--text);
    border: 0;
    border-radius: 1rem;
    font: inherit;
    font-size: 1rem;
    caret-color: var(--text);
    box-shadow: inset 4px 4px 8px #bcbcbc, inset -4px -4px 8px var(--highlight);
  }}

  .field-input::placeholder {{ color: var(--text-muted); opacity: 1; }}

  .field-input:focus-visible {{
    outline: none;
    background: #d6d6d6;
    box-shadow: inset 5px 5px 10px #b4b4b4, inset -5px -5px 10px var(--highlight);
  }}

  .field-input[aria-invalid="true"] {{
    box-shadow: inset 4px 4px 8px #c6a5a2, inset -4px -4px 8px var(--highlight);
  }}

  .form-error {{
    padding: 0.75rem 0.9rem;
    border-radius: 0.8rem;
    background: var(--surface);
    box-shadow: inset 1px 1px 3px #c6a5a2, inset -1px -1px 3px var(--highlight);
    color: var(--danger);
    font-size: 0.82rem;
    line-height: 1.45;
    text-align: start;
  }}

  .provider-form .provider-btn {{ margin-top: 0.5rem; }}

  .foot {{
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 1.8rem 0 0;
    color: var(--text-muted);
    font-size: 0.75rem;
  }}

  .status-dot {{
    width: 0.42rem;
    height: 0.42rem;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--lime);
    animation: breathe 3.2s ease-in-out infinite;
  }}

  @keyframes breathe {{
    0%, 100% {{ box-shadow: 0 0 0 0.16rem rgba(158, 222, 1, 0.18); }}
    50%      {{ box-shadow: 0 0 0 0.34rem rgba(158, 222, 1, 0.32); }}
  }}

  ::selection {{
    background: var(--lime);
    color: var(--lime-text);
  }}

  @media (hover: hover) {{
    .provider-btn:hover {{
      transform: translateY(-1px);
      filter: brightness(1.04);
    }}
  }}

  @media (max-width: 860px) {{
    main {{ padding: 2.5rem clamp(1.25rem, 7vw, 4rem); }}
    .login-shell {{
      max-width: 32rem;
      grid-template-columns: 1fr;
      gap: 2.6rem;
    }}
    .brand-lockup {{ margin-bottom: 1.6rem; }}
    h1 {{ max-width: 20ch; font-size: clamp(2rem, 7vw, 3rem); }}
  }}

  @media (max-width: 480px) {{
    main {{ padding: 1.75rem 1.15rem 2.5rem; }}
    .login-shell {{ gap: 2rem; }}
    .card {{ padding: 1.5rem; border-radius: 1.5rem; }}
    h2 {{ margin-bottom: 1.5rem; }}
  }}

  @media (forced-colors: active) {{
    .provider-btn,
    .field-input:focus-visible {{
      outline: 2px solid CanvasText;
      outline-offset: 3px;
    }}
  }}

  @media (prefers-reduced-motion: reduce) {{
    .login-shell {{ animation: none; }}
    .status-dot {{ animation: none; }}
    .tagline-line {{ animation-name: tagline-plain; }}
    .provider-btn {{ transition: none; }}
    .provider-btn:hover,
    .provider-btn:active {{ transform: none; }}
  }}
</style>
</head>
<body>
<main>
  <div class="login-shell">
    <section class="identity">
      <div class="brand-lockup">
        <img class="brand-word" src="{base_path}/brand/korra-wordmark.png?v=0.21.14" alt="Korra" width="1144" height="235">
        <img class="brand-number" src="{base_path}/brand/korra-21.png?v=0.21.14" alt="21" width="423" height="261">
      </div>
      <h1>
        <span class="tagline-line">Чтобы твои идеи не оставались идеями</span>
        <span class="tagline-line" aria-hidden="true">Дай своим идеям место в реальности</span>
        <span class="tagline-line" aria-hidden="true">Реальность ждёт твоих творений</span>
        <span class="tagline-line" aria-hidden="true">Мир ещё не видел того, что ты создашь</span>
        <span class="tagline-line" aria-hidden="true">То, чего ещё нет, начинается с тебя</span>
      </h1>
    </section>
    <section class="card" aria-labelledby="greeting">
      <h2 id="greeting">{greeting}</h2>
      <div class="provider-list">
{provider_buttons}
      </div>
      <p class="foot"><span class="status-dot" aria-hidden="true"></span>korra-agent.online</p>
    </section>
  </div>
</main>
{password_script}
</body>
</html>
"""

_EMPTY_HTML = """\
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="data:image/png;base64,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b0FxGvp+UR5+VmhMGE58bhm5rov2B7M4tCJqWDG7kQatLLPKWMkUmPJvA4LBo7qARNyXeMITLxnWbBfiaWHdU+J8sTQPgK+NpIL2OeFFDXkvx4Xkt8yaPKCUpACADRVVRQRsWbl5Tokm3bzcDWVyuLYP6xKI2DhUMi94Oc6mvmKosRsVy5Q/Qi7SjyBktwhNyyodUKCppaoI2ACsXbICDtS5XQilRHfiR0YjfVgJuNx0nPPJIxRkghXrhZff99h2QLAW1u80PP2qQTyptGjqRm0TyvzMQGaZ8ocdHRXGGwacMUNh826hpVNItwtb9xr2vWFILXdkjA/5MQhLS1RAgaZgGY22yamBR6a82vmh2gfUNZ10uxCUlOaV8Nw7huaVDlzEST/UrYSBzvWO+/aAYJj0h7ES0h/7jbGGfgS3QqphLZSLYHxhzXZDPC3z9RaYHlGuntK5//dNoNrhfk0fZtK/RabcgacRomEKT333XlQlu1nofExQy1yfEpl3/NmjynCvIjXTrNUJVbASUvDGGYxeZMvUPp4cf4VAikz7o1yJnaS04ib73jS0dQk2/MsyE7h1Rbl2xtIQFUoFXZoC1ToLwrg/QGCKRGyciMYBSJSXs26H6/1aqbcuVABoysD+9zxeeNeQaK29SanTih2TKf82E/4QraUuimaK88mjFJhk3WqDH3EvHwDjQVhWcv0QiUM8DUHZETJLLcFCLwSmyLX4KZJBG/EwQzzMkPcnuHpK2bpPSS13b7zb1+D3ry3dx5RIDBKtMJuH0etQLtbemNxDAUfjdqSXc8kjrC3swBJiDNw4qxw6GJJqE2yojPTB9KhrxwqM9FVmm/qb1CqBGaC0OLygogxFu8lFrhNIGRBUhZE+ZeSP6k4YjFfdJ9wz5vCq1ekDLtUrBUDJzGAlQFQqtRWM5646e7/F4jSQmyMgImPAR6o6Xo9E9XM/KdYKVe0FPgFCULyxqVEqh5Nu4DqwFkhX0rYP5FIsMA2cAN42njnpjmaXFx5ON4KqINIGbAZS/yTTO9N2vgQuichE9Vz4v4g/ASlFZPGB5P9IAAAAFHRFWHR4bXA6Q29sb3JTcGFjZQA2NTUzNTtUTfIAAABEdEVYdHhtcDpDcmVhdG9yVG9vbABDYW52YSBkb2M9REFISlJmYTREancgdXNlcj1VQUZzYnh3UjdyRSBicmFuZD1UIEhBU0RGcgbz3AAAABR0RVh0eG1wOkV4aWZWZXJzaW9uADAyMTCwWRvkAAAAGHRFWHR4bXA6Rmxhc2hQaXhWZXJzaW9uADAxMDCBx9h8AAAAGHRFWHR4bXA6UGl4ZWxYRGltZW5zaW9uADEyMDBXCdQ6AAAAGHRFWHR4bXA6UGl4ZWxZRGltZW5zaW9uADEyMDDu8g/SAAAAAElFTkSuQmCC">
<meta name="theme-color" content="#e8e8e8">
<title>Настройка входа — Korra21</title>
<style>
  @font-face {
    font-family: 'Onest';
    font-style: normal;
    font-weight: 100 900;
    font-display: swap;
    src: url('/fonts/Onest-Variable.woff2') format('woff2');
  }
  :root {
    color-scheme: light;
    --canvas: #e8e8e8;
    --surface: #e0e0e0;
    --text: #1f1f1f;
    --muted: #5c5c5c;
    --lime: #9ede01;
    --shadow: #bebebe;
    --highlight: #ffffff;
  }
  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; min-height: 100%;
    background: var(--canvas);
    color: var(--text);
    font-family: 'Onest', sans-serif;
    font-size: 16px; line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  body {
    min-height: 100vh;
    min-height: 100svh;
    display: grid;
    place-items: center;
    padding: clamp(1.25rem, 5vw, 4rem);
    background:
      radial-gradient(circle at 15% 12%, #f4f4f4, transparent 34rem),
      var(--canvas);
  }
  /* Одна центрированная колонна, как и на /login: карточки тут нет, чтобы
     служебная страница и вход выглядели одним продуктом. */
  main {
    width: 100%; max-width: 30rem;
  }
  /* Тот же лок-ап из PNG, что и на /login: множитель .86 уравнивает
     оптические высоты литер и цифры внутри их кадров. */
  .brand {
    --word-h: 2.75rem;
    display: inline-flex;
    align-items: center;
    gap: 0.1rem;
    margin-bottom: clamp(2.4rem, 7vh, 3.6rem);
  }
  .brand-word { height: var(--word-h); width: auto; }
  .brand-number { height: calc(var(--word-h) * 0.86); width: auto; }
  h1 {
    margin: 0 0 1rem;
    font-weight: 680;
    font-size: clamp(2rem, 6vw, 2.85rem);
    line-height: 1.08;
    letter-spacing: -0.04em;
  }
  p { margin: 0 0 1rem; color: var(--muted); }
  .next-step { margin-top: 2rem; color: var(--text); }
  /* Лайм на светлом фоне читается только заливкой, поэтому встроенный код и
     ссылка идут чернилами: канон `UI_PALETTE.md`, раздел «Акцент». */
  code {
    display: inline-block;
    padding: 0.18em 0.45em;
    border-radius: 0.45rem;
    background: var(--surface);
    box-shadow: inset 1px 1px 3px var(--shadow), inset -1px -1px 3px var(--highlight);
    color: var(--text);
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.88em;
  }
  a {
    display: inline-flex;
    min-height: 2.75rem;
    align-items: center;
    margin-top: 0.5rem;
    color: var(--text);
    font-weight: 650;
    text-underline-offset: 0.24em;
  }
  a:focus-visible {
    outline: 2px solid currentColor;
    outline-offset: 4px;
  }
</style>
</head>
<body>
<main>
<div class="brand">
  <img class="brand-word" src="/brand/korra-wordmark.png?v=0.21.14" alt="Korra" width="1144" height="235">
  <img class="brand-number" src="/brand/korra-21.png?v=0.21.14" alt="21" width="423" height="261">
</div>
<h1>Настройте доступ</h1>
<p>Панель готова принимать подключения. Добавьте провайдер логина и пароля или OAuth-провайдер в конфигурации Korra21.</p>
<p class="next-step">Для локальной работы привяжите панель к <code>127.0.0.1</code> и откройте её через защищённый туннель: SSH-туннель или Tailscale.</p>
<a href="https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard#authentication-gated-mode">Инструкция по настройке входа</a>
</main>
</body>
</html>
"""


# Inline script that wires every password provider form to POST JSON to
# ``/auth/password-login`` and navigate on success. Emitted ONLY when at
# least one ``supports_password`` provider is listed (OAuth-only login
# pages stay script-free, preserving the no-JS contract for that case).
#
# Plain string (NOT run through ``str.format``), so braces are literal —
# do not double them. A single delegated submit handler covers all forms;
# the provider name is read from the form's ``data-provider`` attribute.
_PASSWORD_FORM_SCRIPT = """\
<script>
(function () {
  function handle(form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var err = form.querySelector('.form-error');
      var btn = form.querySelector('button[type=submit]');
      var fields = form.querySelectorAll('.field-input');
      if (err) { err.hidden = true; err.textContent = ''; }
      for (var i = 0; i < fields.length; i++) {
        fields[i].removeAttribute('aria-invalid');
      }
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Входим…';
      }
      form.setAttribute('aria-busy', 'true');
      var body = {
        provider: form.getAttribute('data-provider') || '',
        username: (form.querySelector('input[name=username]') || {}).value || '',
        password: (form.querySelector('input[name=password]') || {}).value || '',
        next: (form.querySelector('input[name=next]') || {}).value || ''
      };
      fetch(form.action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'same-origin'
      }).then(function (resp) {
        if (resp.ok) {
          return resp.json().then(function (data) {
            window.location.assign((data && data.next) || '/');
          });
        }
        var msg = resp.status === 429
          ? 'Слишком много попыток. Подождите немного и повторите вход.'
          : (resp.status === 401 ? 'Проверьте логин и пароль.'
                                 : 'Не удалось войти. Повторите попытку.');
        if (err) { err.textContent = msg; err.hidden = false; }
        if (resp.status === 401) {
          for (var i = 0; i < fields.length; i++) {
            fields[i].setAttribute('aria-invalid', 'true');
          }
          if (fields.length) { fields[0].focus(); }
        }
        if (btn) { btn.disabled = false; btn.textContent = 'Войти'; }
        form.removeAttribute('aria-busy');
      }).catch(function () {
        if (err) { err.textContent = 'Соединение прервалось. Проверьте интернет и повторите вход.'; err.hidden = false; }
        if (btn) { btn.disabled = false; btn.textContent = 'Войти'; }
        form.removeAttribute('aria-busy');
      });
    });
  }
  var forms = document.querySelectorAll('form.provider-form');
  for (var i = 0; i < forms.length; i++) { handle(forms[i]); }
})();
</script>
"""


def greeting_for_hour(hour: int) -> str:
    """Приветствие по часу суток.

    Ночь тянется до пяти утра осознанно: владельцы контуров работают и ночами,
    а «Доброе утро» в три часа читается как сбой, не как приветствие.
    """
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def current_greeting() -> str:
    """Приветствие в часовом поясе контура.

    Берём ``korra_time.now()`` — тот же источник, что и расписание агента
    (``HERMES_TIMEZONE`` → ``timezone`` в конфиге → локальное время). Контейнер
    обычно живёт в UTC, поэтому спрашивать системные часы напрямую нельзя:
    владелец получил бы «Доброй ночи» в своё рабочее утро. Уточнять время
    скриптом здесь не годится — OAuth-вариант страницы обязан оставаться
    без JavaScript.
    """
    try:
        from korra_time import now as korra_now

        return greeting_for_hour(korra_now().hour)
    except Exception:  # pragma: no cover — часы никогда не ломают вход
        from datetime import datetime

        return greeting_for_hour(datetime.now().astimezone().hour)


def render_login_html(*, next_path: str = "", base_path: str = "") -> str:
    """Return the full HTML for ``GET /login``.

    ``next_path`` — when set, the post-login landing path the user
    originally requested. Threaded into each provider button's ``href``
    as a ``next=`` query parameter so the OAuth round trip carries it
    end-to-end. The caller (``routes.login_page``) is responsible for
    validating ``next_path`` against the same-origin rules before we
    emit it; we still HTML-escape it as defence in depth.

    ``base_path`` is the normalised reverse-proxy prefix. It is applied to
    every pre-auth asset and auth action so the page works both at ``/login``
    and under a cabinet/ingress mount such as ``/hermes/login``.
    """
    from urllib.parse import quote

    # A proxy prefix is still untrusted input. Keep slashes as path separators,
    # but percent-encode characters that HTML/WHATWG URL parsing could reinterpret
    # (for example ``&#92;`` becoming a backslash in a form action).
    prefix = quote(normalise_prefix(base_path), safe="/")
    providers = list_session_providers()
    if not providers:
        return _EMPTY_HTML.replace(
            "url('/fonts/", f"url('{prefix}/fonts/"
        ).replace('src="/brand/', f'src="{prefix}/brand/')

    if next_path:
        # URL-encode then HTML-escape. The URL-encode step matches the
        # gate's ``_safe_next_target`` output shape (also URL-encoded),
        # so a value that round-tripped from /login?next=... back into
        # the button href is byte-identical.
        next_qs = f"&next={html.escape(quote(next_path, safe=''), quote=True)}"
    else:
        next_qs = ""

    buttons = []
    needs_password_script = False
    for p in providers:
        if getattr(p, "supports_password", False):
            needs_password_script = True
            buttons.append(_render_password_form(p, next_path, prefix))
        else:
            buttons.append(
                f'      <a class="provider-btn" '
                f'href="{prefix}/auth/login?provider={html.escape(p.name, quote=True)}{next_qs}">'
                f'Продолжить через {html.escape(p.display_name)}</a>'
            )
    script = _PASSWORD_FORM_SCRIPT if needs_password_script else ""
    return _LOGIN_HTML_TEMPLATE.format(
        provider_buttons="\n".join(buttons),
        password_script=script,
        base_path=prefix,
        greeting=current_greeting(),
    )


def _render_password_form(provider, next_path: str, base_path: str) -> str:
    """Render a username/password form for a ``supports_password`` provider.

    The form is wired by :data:`_PASSWORD_FORM_SCRIPT` (a single delegated
    submit handler) to POST JSON to ``/auth/password-login`` and navigate
    on success. ``next_path`` is carried in a hidden field; it has already
    been validated same-origin by the caller and is HTML-escaped here as
    defence in depth. The provider ``name`` is emitted in a ``data-``
    attribute (not a hidden input) so the script reads it without trusting
    form-field ordering.
    """
    pname = html.escape(provider.name, quote=True)
    error_id = f"login-error-{pname}"
    safe_next = html.escape(next_path, quote=True) if next_path else ""
    return (
        f'      <form class="provider-form" data-provider="{pname}" '
        f'method="post" action="{base_path}/auth/password-login" autocomplete="on">\n'
        f'        <input type="hidden" name="next" value="{safe_next}">\n'
        f'        <div class="form-error" id="{error_id}" role="alert" hidden></div>\n'
        f'        <label class="field">\n'
        f'          <span class="field-label">Логин</span>\n'
        f'          <input class="field-input" type="text" name="username" '
        f'placeholder="Логин" '
        f'aria-describedby="{error_id}" '
        f'autocomplete="username" autocapitalize="none" '
        f'autocorrect="off" spellcheck="false" required>\n'
        f'        </label>\n'
        f'        <label class="field">\n'
        f'          <span class="field-label">Пароль</span>\n'
        f'          <input class="field-input" type="password" name="password" '
        f'placeholder="Пароль" '
        f'aria-describedby="{error_id}" '
        f'autocomplete="current-password" required>\n'
        f'        </label>\n'
        f'        <button class="provider-btn" type="submit">Войти</button>\n'
        f'      </form>'
    )
