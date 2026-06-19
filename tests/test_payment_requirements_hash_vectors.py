import json
from pathlib import Path

from x402_proxy.internal_facilitator import (
    InternalPaymentContext,
    _build_xrpl_binding,
    _fingerprint,
)

FIXTURE_PATH = Path(__file__).parent.parent / "test-vectors" / "payment_requirements_hash.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_payment_requirements_hash_fixture_is_shared_contract() -> None:
    fixture = _fixture()

    assert fixture["version"] == "payment-requirements-hash.v1"
    assert len(fixture["vectors"]) >= 6


def test_internal_facilitator_fingerprint_matches_shared_vectors() -> None:
    for vector in _fixture()["vectors"]:
        payment_requirements = vector["paymentRequirements"]

        assert _fingerprint(payment_requirements) == vector["expectedHash"]


def test_xrpl_binding_fallback_hash_matches_shared_vectors() -> None:
    for vector in _fixture()["vectors"]:
        payment_requirements = vector["paymentRequirements"]
        payment = InternalPaymentContext(
            chain="xrpl",
            network=payment_requirements["network"],
            asset=payment_requirements["asset"],
            amount=payment_requirements["amount"],
            destination=payment_requirements["payTo"],
            paymentRequirements=payment_requirements,
            payload={
                "Destination": payment_requirements["payTo"],
                "Amount": payment_requirements["amount"],
            },
        )

        binding = _build_xrpl_binding(payment)

        assert binding.hashes["paymentRequirementsHash"] == vector["expectedHash"]


def test_xrpl_binding_preserves_facilitator_supplied_hash() -> None:
    payment = InternalPaymentContext(
        chain="xrpl",
        network="xrpl:0",
        asset="XRP",
        amount="1000",
        destination="rMerchant",
        paymentRequirements={"extra": {"invoiceId": "INV-LOCAL"}},
        paymentRequirementsHash="sha256:facilitator-supplied",
        payload={"Destination": "rMerchant", "Amount": "1000"},
    )

    binding = _build_xrpl_binding(payment)

    assert binding.hashes["paymentRequirementsHash"] == "sha256:facilitator-supplied"
