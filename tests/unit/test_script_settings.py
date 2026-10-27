from pathlib import Path

import configs
from scripts.settings import OfflineSettings


def test_offline_settings_ignore_environment_and_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MAX_CELLS=invalid\nLLM_BASE_URL=invalid", encoding="utf-8")
    for key, value in {
        "LLM_PROVIDER": "openai",
        "ALLOW_REMOTE_LLM": "true",
        "OPENAI_API_KEY": "synthetic-unwanted-credential",
        "LLM_BASE_URL": "invalid",
        "MAX_REQUEST_BYTES": "invalid",
        "FORM_CONFIG_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)

    settings = OfflineSettings()

    assert settings.llm_provider == "mock"
    assert settings.allow_remote_llm is False
    assert not settings.openai_api_key.get_secret_value()
    assert settings.llm_base_url == ""
    assert settings.max_request_bytes == 2_000_000
    assert settings.form_config_dir == Path(configs.__file__).parent / "forms"
