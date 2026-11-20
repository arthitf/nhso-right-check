from pathlib import Path

from nhso_right_check import agent_doctor as ad


def _fake_agent(tmp_path: Path, ver: str = "1.8.0_202") -> Path:
    d = tmp_path / "agent"
    (d / "jre").mkdir(parents=True)
    (d / "secureagent.jar").write_text("x")
    (d / "jre" / "release").write_text(f'JAVA_VERSION="{ver}"\nOS_NAME="Windows"\n')
    return d


def test_java_update():
    assert ad.java_update("1.8.0_202") == 202
    assert ad.java_update("1.8.0_472") == 472
    assert ad.java_update("11.0.2") is None
    assert ad.java_update("") is None


def test_find_and_version(tmp_path):
    d = _fake_agent(tmp_path)
    assert ad.find_agent_dir(str(d)) == d
    assert ad.find_agent_dir(str(tmp_path / "nope")) in (None, ad.find_agent_dir(""))
    assert ad.java_version(d) == "1.8.0_202"
    assert ad.java_version(d, "jre_old") == ""


def test_diagnose_flags_old_java_and_pkix(tmp_path, monkeypatch):
    d = _fake_agent(tmp_path)
    monkeypatch.setattr(ad, "IS_WINDOWS", True)
    monkeypatch.setattr(ad, "service_info", lambda: {"installed": True, "state": "RUNNING", "dir": str(d)})
    monkeypatch.setattr(ad, "result_file", lambda: tmp_path / "none.json")
    r = ad.diagnose("", True, "I/O error ... PKIX path building failed")
    assert r["agentDir"] == str(d) and r["javaOld"] and r["canFix"] and not r["hasBackup"]
    assert any("รุ่นเก่า" in i for i in r["issues"]) and any("PKIX" in i for i in r["issues"])
    (d / "jre" / "release").write_text('JAVA_VERSION="1.8.0_472"')
    (d / "jre_old").mkdir()
    (d / "jre_old" / "release").write_text('JAVA_VERSION="1.8.0_202"')
    r = ad.diagnose("", True, "")
    assert not r["javaOld"] and r["issues"] == [] and r["hasBackup"] and r["backupVersion"] == "1.8.0_202"


def test_diagnose_non_windows(monkeypatch):
    monkeypatch.setattr(ad, "IS_WINDOWS", False)
    r = ad.diagnose("")
    assert not r["canFix"] and "Windows" in r["issues"][0]


def test_launch_refuses_off_windows(monkeypatch):
    import pytest
    monkeypatch.setattr(ad, "IS_WINDOWS", False)
    with pytest.raises(RuntimeError):
        ad.launch("fix")
    monkeypatch.setattr(ad, "IS_WINDOWS", True)
    with pytest.raises(ValueError):
        ad.launch("delete")


def test_last_result_reads_bom_json(tmp_path, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text('{"ok": true, "mode": "fix", "message": "อัปเดตแล้ว"}', encoding="utf-8-sig")
    monkeypatch.setattr(ad, "result_file", lambda: f)
    r = ad.last_result()
    assert r["ok"] and r["mode"] == "fix" and r["mtime"] > 0


def test_web_agent_diag_saves_dir(tmp_path, monkeypatch):
    from nhso_right_check.config import Settings
    from nhso_right_check.validation import ValidationError
    from nhso_right_check.web import App
    import pytest
    monkeypatch.chdir(tmp_path)
    d = _fake_agent(tmp_path)
    (tmp_path / ".env").write_text("NHSO_ENV=test\n", encoding="utf-8")
    monkeypatch.delenv("AGENT_DIR", raising=False)
    app = App(Settings.load(tmp_path / ".env"))
    monkeypatch.setattr(app, "agent_status", lambda _b: {"online": False})
    r = app.agent_diag({"dir": str(d)})
    assert r["agentDir"] == str(d) and r["javaVersion"] == "1.8.0_202"
    assert f"AGENT_DIR={d}" in (tmp_path / ".env").read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        app.agent_diag({"dir": "C:\\x & calc"})
    monkeypatch.delenv("AGENT_DIR", raising=False)
