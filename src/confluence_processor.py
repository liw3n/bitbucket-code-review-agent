from typing_extensions import List
import re
import requests
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv
import os
import base64
from requests.auth import HTTPBasicAuth

class ConfluenceProcessor:
    def __init__(self) -> None:
        load_dotenv()
        self.access_token = os.environ["CONFLUENCE_ACCESS_TOKEN"]
        self.username = os.environ["CONFLUENCE_USERNAME"]
        self.confluence_link = os.environ["CONFLUENCE_LINK"]
        self.encoded_token = self._encode_token()

    def get_confluence_content(self, confluence_links: List[str]) -> str:
        full_content = ""
        for link in confluence_links:
            try:
                # Get id 
                path = unquote(urlparse(link).path)
                id_match = re.search(r'/pages/(\d+)(?:/|$)', path)
                id_match = id_match.group(1) if id_match else None

                url = f"https://{self.confluence_link}/wiki/api/v2/pages/{id_match}"
                auth = HTTPBasicAuth(self.username, self.access_token)

                headers = {
                "Accept": "application/json",
                # "Authorization": f"Basic {self.encoded_token}"
                }

                response = requests.request("GET", url, headers=headers, auth=auth)
                response.raise_for_status()
                full_content += "\n" + response.text
            except Exception as e:
                continue
        return full_content
    
    def _encode_token(self) -> str:
        full_token = f"{self.username}:{self.access_token}".encode('utf-8')
        encoded_full_token = base64.b64encode(full_token)
        return encoded_full_token.decode('utf-8')