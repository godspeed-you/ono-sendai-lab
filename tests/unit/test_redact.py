from ono_sendai_lab.redact import REDACTED, redact_obj, redact_text

KEY = "EMULATOR0123456789AB"
SECRET = "0123456789ABCDEF0123456789ABCDEF01234567"


def test_emulator_key_and_secret_are_redacted():
    text = f"aws_access_key_id = {KEY}\naws_secret_access_key = {SECRET}\n"
    out = redact_text(text)
    assert KEY not in out and SECRET not in out
    assert out.count(REDACTED) >= 2


def test_bare_tokens_redacted_anywhere():
    assert KEY not in redact_text(f"key {KEY} used")
    assert SECRET not in redact_text(f"secret={SECRET}")
    aws_like = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert aws_like not in redact_text(f"value {aws_like} end")
    assert "AKIAIOSFODNN7EXAMPLE" not in redact_text("AKIAIOSFODNN7EXAMPLE")


def test_signature_material_redacted():
    header = (
        "Authorization: AWS4-HMAC-SHA256 Credential=EMULATOR0123456789AB/20261124/us-west-2/sts/aws4_request, "
        "Signature=abcdef0123456789abcdef"
    )
    out = redact_text(header)
    assert "Credential=EMULATOR" not in out and "abcdef0123456789abcdef" not in out
    url = "https://h/b/k?X-Amz-Credential=XYZ%2F1&X-Amz-Signature=deadbeefdeadbeef&X-Amz-Security-Token=tok123"
    out = redact_text(url)
    assert "deadbeefdeadbeef" not in out and "XYZ%2F1" not in out and "tok123" not in out


def test_pem_private_key_redacted():
    marker = "PRIVATE" + " KEY"  # assembled so the repository secret scan stays clean
    pem = f"-----BEGIN {marker}-----\nMIIabc\n-----END {marker}-----"
    assert "MIIabc" not in redact_text(pem)


def test_long_tokens_redacted():
    token = "A" * 150
    assert token not in redact_text(f"token {token}")


def test_git_commit_and_sha256_stay_readable():
    commit = "fc05151cc5015eb457b31a79871539af51fb9fdd"
    digest = "950eeab681c2cd4e9167a8dbe8ad9a74e0d6ad51aad86dfb5660245bbbd1f20f"
    assert redact_text(commit) == commit
    assert redact_text(digest) == digest


def test_sensitive_keys_in_objects():
    obj = {"SecretAccessKey": "x", "nested": [{"Authorization": "y", "ok": "fine"}], "token": "t"}
    out = redact_obj(obj)
    assert out["SecretAccessKey"] == REDACTED
    assert out["nested"][0]["Authorization"] == REDACTED
    assert out["nested"][0]["ok"] == "fine"
    assert out["token"] == REDACTED
