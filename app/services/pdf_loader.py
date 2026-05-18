import tempfile

import requests


def download_pdf(file_url: str) -> str:
    try:
        response = requests.get(file_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not download PDF: {exc}") from exc

    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    temp_file.write(response.content)
    temp_file.flush()
    temp_file.close()

    return temp_file.name
