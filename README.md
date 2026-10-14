# 금융거래확인서 자동화 · Streamlit 배포 파일

## 배포 전 설정

Streamlit Community Cloud의 **App settings → Secrets**에 아래 값을 등록합니다.

```toml
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
```

`.env` 파일과 실제 API 키는 이 폴더에 포함하지 않습니다.

## 실행 명령

```powershell
pip install -r requirements.txt
streamlit run streamlit_app.py
```

배포 시 시작 파일은 `streamlit_app.py`입니다.
