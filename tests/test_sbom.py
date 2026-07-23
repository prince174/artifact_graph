from app.sbom import summarize_sbom


def test_summarizes_cyclonedx_sbom():
    result = summarize_sbom('{"bomFormat":"CycloneDX","specVersion":"1.6","serialNumber":"urn:uuid:1","components":[{},{}]}')
    assert result == {
        "sbomStatus": "valid", "bomFormat": "CycloneDX", "specVersion": "1.6",
        "serialNumber": "urn:uuid:1", "componentCount": 2,
    }


def test_marks_invalid_unavailable_and_oversized_sbom():
    assert summarize_sbom("not-json")["sbomStatus"] == "invalid_json"
    assert summarize_sbom("[]")["sbomStatus"] == "invalid_document"
    assert summarize_sbom(None)["sbomStatus"] == "unavailable"
    assert summarize_sbom("{}", truncated=True)["sbomStatus"] == "too_large"
