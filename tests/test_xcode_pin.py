from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github/workflows/build.yml"
ATTESTATION = ROOT / ".github/workflows/source-attestation.yml"


def test_apple_release_build_uses_xcode_26_6() -> None:
    workflow = BUILD.read_text(encoding="utf-8")
    assert workflow.count('"os":"macos-26"') == 2
    assert '"os":"macos-latest"' not in workflow
    assert "if: matrix.platform == 'ios' || matrix.platform == 'macos'" in workflow
    assert "DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer" in workflow
    assert "xcodebuild -version" in workflow
    assert "Xcode 26.6" in workflow
    assert 'echo "DEVELOPER_DIR=$DEVELOPER_DIR" >> "$GITHUB_ENV"' in workflow


def test_macos_attestation_uses_xcode_26_6() -> None:
    workflow = ATTESTATION.read_text(encoding="utf-8")
    assert "runs-on: macos-latest" not in workflow
    assert "runs-on: macos-26" in workflow
    assert "DEVELOPER_DIR: /Applications/Xcode_26.6.app/Contents/Developer" in workflow
    assert "xcodebuild -version" in workflow
    assert "Xcode 26.6" in workflow
    assert 'echo "DEVELOPER_DIR=$DEVELOPER_DIR" >> "$GITHUB_ENV"' in workflow
