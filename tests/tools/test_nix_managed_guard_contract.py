"""Static regression checks for the Nix managed-install acceptance probe."""

from pathlib import Path


CHECKS = Path(__file__).parents[2] / "nix" / "checks.nix"


def _managed_guard_block(source: str) -> str:
    start = source.index("        managed-guard =")
    end = source.index("        # Verify extraPythonPackages", start)
    return source[start:end]


def _assert_locale_neutral_refusal_contract(block: str) -> None:
    assert 'MANAGED_SYSTEM="nixos"' in block
    assert 'STATUS=$?' in block
    assert 'if [ "$STATUS" -ne 0 ]' in block
    assert 'grep -Eqi -- "(^|[^[:alnum:]_-])$MANAGED_SYSTEM' in block
    assert 'cmp -s "$CONFIG_BASELINE" "$CONFIG_FILE"' in block
    assert 'test ! -e "$EDITOR_CALLED"' in block
    assert "managed by nixos" not in block
    assert '2>&1 || true' not in block


def test_managed_guard_is_locale_neutral_and_checks_refusal_effects() -> None:
    block = _managed_guard_block(CHECKS.read_text(encoding="utf-8"))

    _assert_locale_neutral_refusal_contract(block)


def test_contract_rejects_the_previous_arbitrary_nonzero_acceptance() -> None:
    block = _managed_guard_block(CHECKS.read_text(encoding="utf-8"))
    weakened = block.replace(
        'OUTPUT=$(HERMES_MANAGED="$MANAGED_SYSTEM"',
        'OUTPUT=$(HERMES_MANAGED="$MANAGED_SYSTEM"',
    ).replace(
        ' 2>&1)\n            STATUS=$?',
        ' 2>&1 || true)\n            STATUS=$?',
    )

    try:
        _assert_locale_neutral_refusal_contract(weakened)
    except AssertionError:
        pass
    else:
        raise AssertionError("contract accepted a probe that discards command status")
