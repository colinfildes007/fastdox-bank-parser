import tempfile
from pathlib import Path

import requests


def download_pdf(file_url: str, timeout: int = 30) -> Path:
    response = requests.get(file_url, timeout=timeout)
    response.raise_for_status()

    temp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    temp_file.write(response.content)
    temp_file.flush()
    temp_file.close()

    return Path(temp_file.name)
