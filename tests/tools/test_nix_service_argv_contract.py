"""Static contract for the Nix service argv probe."""

from pathlib import Path

CHECKS_NIX = Path(__file__).resolve().parents[2] / "nix/checks.nix"


def _service_argv_block(text: str) -> str:
    start = text.index("        service-argv =")
    end = text.index("        # Verify binaries exist", start)
    return text[start:end]


def _has_locale_neutral_exact_probe_contract(text: str) -> bool:
    block = _service_argv_block(text)
    return (
        '*": ${sentinel}")' in block
        and '*"${sentinel}"*)' in block
        and "argv ++ [ sentinel ]" in block
    )


def test_service_argv_probe_contract_is_locale_neutral_and_exact() -> None:
    text = CHECKS_NIX.read_text(encoding="utf-8")
    assert _has_locale_neutral_exact_probe_contract(text)


def test_gate_rejects_the_english_only_pre_fix_contract() -> None:
    text = CHECKS_NIX.read_text(encoding="utf-8")
    pre_fix = text.replace(
        '*": ${sentinel}")',
        '*"unrecognized arguments: ${sentinel}")',
    )
    assert pre_fix != text
    assert not _has_locale_neutral_exact_probe_contract(pre_fix)
